"""Extractor: Public API for XLStruct.

Orchestrates the full pipeline: Storage → Reader → Encoder → Engine.
Delegates code generation to CodegenOrchestrator.
"""

import asyncio
import logging
import re
from collections.abc import Callable
from pathlib import Path as PathLibPath
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from pandas import DataFrame  # type: ignore[import-untyped]

from pydantic import BaseModel, SecretStr

from xlstruct.codegen.backends.base import ExecutionBackend
from xlstruct.codegen.cache import ScriptCache, compute_structure_signature
from xlstruct.codegen.orchestrator import CodegenOrchestrator
from xlstruct.config import (
    SAMPLE_ROWS,
    ExtractionConfig,
    ExtractionMode,
    ExtractorConfig,
    apply_cache_control,
    build_instructor_client,
)
from xlstruct.encoder.compressed import CompressedEncoder
from xlstruct.exceptions import ErrorCode, ReaderError
from xlstruct.extraction.chunking import ChunkSplitter, needs_chunking
from xlstruct.extraction.engine import ExtractionEngine
from xlstruct.reader.hybrid_reader import HybridReader
from xlstruct.schemas.batch import BatchResult, FileResult
from xlstruct.schemas.codegen import GeneratedScript
from xlstruct.schemas.core import SheetData, WorkbookData
from xlstruct.schemas.progress import ProgressEvent, ProgressStatus
from xlstruct.schemas.report import ExtractionReport
from xlstruct.schemas.usage import UsageTracker
from xlstruct.schemas.workbook import SheetResult, WorkbookResult
from xlstruct.storage import read_file

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class ExtractionResult(list[T]):  # type: ignore[type-var,unused-ignore]
    """List of extracted records with an attached extraction report.

    Behaves exactly like list[T] (iteration, indexing, len, etc.)
    but also exposes a ``.report`` attribute containing extraction metadata
    (mode used, token usage, provenance, etc.).
    """

    report: ExtractionReport

    def __init__(self, items: list[T], report: ExtractionReport) -> None:
        super().__init__(items)
        self.report = report

    def to_dataframe(self) -> "DataFrame":
        """Convert extracted records to a pandas DataFrame.

        Requires pandas to be installed: ``pip install xlstruct[pandas]``

        Returns:
            pandas DataFrame with one row per extracted record.
        """
        try:
            import pandas as pd
        except ImportError:
            raise ImportError(
                "pandas is required for to_dataframe(). "
                "Install it with: pip install xlstruct[pandas]"
            ) from None

        return pd.DataFrame([item.model_dump() for item in self])


def _run_sync(coro: Any) -> Any:
    """Run a coroutine synchronously, with Jupyter/notebook compatibility.

    Falls back to nest_asyncio when called from inside a running event loop
    (e.g. Jupyter notebook, IPython).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    # ^ Running inside an existing event loop (Jupyter, etc.)
    try:
        import nest_asyncio  # type: ignore[import-not-found]

        nest_asyncio.apply()
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(coro)
    except ImportError:
        raise RuntimeError(
            "Cannot call *_sync() from a running event loop (e.g. Jupyter). "
            "Either use 'await extractor.extract(...)' directly, "
            "or install nest_asyncio: pip install nest_asyncio"
        )


class Extractor:
    """XLStruct main API class.

    Usage:
        extractor = Extractor(provider="anthropic/claude-sonnet-4-6")
        items = await extractor.extract("report.xlsx", schema=InvoiceItem)
    """

    def __init__(
        self,
        provider: str = "anthropic/claude-sonnet-4-6",
        *,
        api_key: str | None = None,
        config: ExtractorConfig | None = None,
        execution_backend: ExecutionBackend | None = None,
        **kwargs: Any,
    ) -> None:
        if config is not None:
            self._config = config
        else:
            secret_key = SecretStr(api_key) if api_key is not None else None
            self._config = ExtractorConfig(provider=provider, api_key=secret_key, **kwargs)

        self._execution_backend = execution_backend
        self._tracker = UsageTracker()
        self._engine = ExtractionEngine(self._config, tracker=self._tracker)
        self._codegen: CodegenOrchestrator | None = None
        self._chunk_splitter = ChunkSplitter()
        self._cache: ScriptCache | None = None
        if self._config.cache_enabled:
            self._cache = ScriptCache(cache_dir=self._config.cache_dir)

    @property
    def cache(self) -> ScriptCache | None:
        """Access the script cache for codegen mode.

        Returns None if caching is disabled (``cache_enabled=False``).
        When enabled, provides ``list_entries()``, ``clear()``, ``remove()`` methods.
        """
        return self._cache

    # * Script export

    def _export_script(self, source: str, script: GeneratedScript) -> PathLibPath | None:
        """Save generated script to export_dir if configured."""
        export_dir = self._config.export_dir
        if export_dir is None:
            return None

        export_dir.mkdir(parents=True, exist_ok=True)

        # ^ Derive filename from source: "report.xlsx" → "report_codegen.py"
        stem = PathLibPath(source.rsplit("/", 1)[-1]).stem
        safe_stem = re.sub(r"[^\w\-]", "_", stem)
        script_path = export_dir / f"{safe_stem}_codegen.py"

        script_path.write_text(script.code, encoding="utf-8")
        logger.info("Exported codegen script: %s", script_path)
        return script_path

    # * Lazy codegen orchestrator

    def _get_codegen(self) -> CodegenOrchestrator:
        if self._codegen is None:
            self._codegen = CodegenOrchestrator(
                self._config, backend=self._execution_backend, tracker=self._tracker
            )
        return self._codegen

    # * Public API

    async def extract(
        self,
        source: str,
        schema: type[T] | None = None,
        *,
        extraction_config: ExtractionConfig | None = None,
        sheet: str | None = None,
        instructions: str | None = None,
        **storage_options: Any,
    ) -> ExtractionResult[T]:
        """Extract structured data from a single sheet.

        Two modes:
        1. Config mode (recommended): Pass ExtractionConfig with header_rows,
           output_schema, etc. Uses 20-row sampling for efficiency.
        2. Legacy mode: Pass schema directly. Auto-detects headers.

        Args:
            source: File path or URL (local, s3://, az://, gs://).
            schema: (Legacy) Pydantic model class defining the target structure.
            extraction_config: Per-extraction config with header_rows, output_schema.
            sheet: Target sheet name. None = first sheet.
            instructions: Optional natural-language hints for the LLM.
            **storage_options: Backend-specific storage options.

        Returns:
            ExtractionResult — list[T] with ``.report`` for extraction metadata.
        """
        self._tracker.reset()

        if extraction_config is not None:
            items, resolved_mode = await self._run_configured_extraction(
                source, extraction_config, **storage_options
            )
        elif schema is not None:
            workbook = await self._load_workbook(source, sheet_name=sheet, **storage_options)
            target_sheet = workbook.sheets[0]
            items = await self._run_sheet_extraction(
                target_sheet, schema, instructions, engine=self._engine
            )
            resolved_mode = ExtractionMode.DIRECT
        else:
            raise ValueError("Either schema or extraction_config must be provided")

        # * Collect provenance from records (set by ExtractionEngine._split_provenance)
        source_rows: list[list[int]] = [getattr(item, "_source_rows", []) for item in items]
        source_cells: list[dict[str, str]] = [getattr(item, "_source_cells", {}) for item in items]
        # ^ Only include if any provenance was actually tracked
        if not any(source_rows):
            source_rows = []
        if not any(source_cells):
            source_cells = []

        # * Collect confidence from records (set by ExtractionEngine.extract)
        field_confidences: dict[str, list[float]] | None = None
        if items and hasattr(items[0], "_field_confidences"):
            all_fields: set[str] = set()
            for item in items:
                per_record = getattr(item, "_field_confidences", {})
                all_fields.update(per_record.keys())
            field_confidences = {name: [] for name in sorted(all_fields)}
            for item in items:
                per_record = getattr(item, "_field_confidences", {})
                for name in field_confidences:
                    field_confidences[name].append(per_record.get(name, 0.5))

        usage = self._tracker.snapshot()
        logger.info(usage)

        report = ExtractionReport(
            mode=resolved_mode,
            usage=usage,
            source_rows=source_rows,
            source_cells=source_cells,
            field_confidences=field_confidences,
        )
        return ExtractionResult(items, report=report)

    async def generate_script(
        self,
        source: str,
        extraction_config: ExtractionConfig,
        **storage_options: Any,
    ) -> GeneratedScript:
        """Generate a standalone transformation script via LLM with self-correction.

        Args:
            source: File path or URL.
            extraction_config: Config with header_rows, output_schema, etc.
            **storage_options: Backend-specific storage options.

        Returns:
            GeneratedScript with code and explanation.
        """
        workbook = await self._load_workbook(
            source, sheet_name=extraction_config.sheet, **storage_options
        )
        full_sheet = workbook.sheets[0]
        codegen = self._get_codegen()

        # * Auto-detect header rows if not provided
        header_rows = extraction_config.header_rows
        if header_rows is None:
            header_rows = await codegen.detect_header_rows(full_sheet)

        script = await codegen.generate_script(source, full_sheet, header_rows, extraction_config)
        self._export_script(source, script)
        return script

    def generate_script_sync(
        self,
        source: str,
        extraction_config: ExtractionConfig,
        **storage_options: Any,
    ) -> GeneratedScript:
        """Synchronous wrapper for generate_script(). Jupyter-compatible."""
        return _run_sync(  # type: ignore[no-any-return]
            self.generate_script(source, extraction_config, **storage_options)
        )

    def extract_sync(
        self,
        source: str,
        schema: type[T] | None = None,
        **kwargs: Any,
    ) -> ExtractionResult[T]:
        """Synchronous wrapper for extract(). Jupyter-compatible."""
        return _run_sync(self.extract(source, schema, **kwargs))  # type: ignore[no-any-return]

    async def suggest_schema(
        self,
        source: str,
        *,
        sheet: str | None = None,
        instructions: str | None = None,
        **storage_options: Any,
    ) -> type[BaseModel]:
        """Analyze an Excel file and suggest a Pydantic schema.

        Returns a dynamically created Pydantic model class that matches
        the spreadsheet structure. Can be passed directly to ``extract()``.

        Args:
            source: File path or URL.
            sheet: Target sheet name. None = first sheet.
            instructions: Hints (e.g. "focus on financial columns").
            **storage_options: Backend-specific storage options.

        Returns:
            A Pydantic model class built via ``pydantic.create_model()``.
        """
        from pydantic import Field, create_model

        workbook = await self._load_workbook(source, sheet_name=sheet, **storage_options)
        target_sheet = workbook.sheets[0]

        encoder = CompressedEncoder(sample_size=SAMPLE_ROWS)
        encoded = encoder.encode(target_sheet)

        hint = ""
        if instructions:
            hint = f"\nAdditional context: {instructions}\n"

        prompt = (
            "Analyze the following spreadsheet data and suggest a Pydantic model.\n\n"
            "Rules:\n"
            "- Return a JSON array of field definitions\n"
            "- Each field: {name (snake_case), type, nullable, description}\n"
            "- type must be one of: str, int, float, bool, date, datetime\n"
            "- description should mention the original Excel column name\n"
            "- model_name: PascalCase name for the model\n"
            f"{hint}\n"
            f"Spreadsheet data:\n{encoded}"
        )

        from xlstruct.prompts.system import SYSTEM_PROMPT
        from xlstruct.schemas.suggest import SuggestedFields

        client = build_instructor_client(self._config)

        messages = apply_cache_control(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            self._config.provider,
        )
        result, completion = await client.create_with_completion(
            response_model=SuggestedFields,
            messages=messages,
            temperature=0.0,
        )
        if self._tracker:
            self._tracker.record("suggest_schema", completion)

        # * Build dynamic Pydantic model via create_model()
        type_map: dict[str, type] = {
            "str": str,
            "int": int,
            "float": float,
            "bool": bool,
            "date": __import__("datetime").date,
            "datetime": __import__("datetime").datetime,
        }

        field_definitions: dict[str, Any] = {}
        for f in result.fields:
            python_type = type_map.get(f.type, str)
            if f.nullable:
                python_type = python_type | None  # type: ignore[assignment]
            field_definitions[f.name] = (
                python_type,
                Field(description=f.description),
            )

        return create_model(result.model_name, **field_definitions)

    def suggest_schema_sync(
        self,
        source: str,
        **kwargs: Any,
    ) -> type[BaseModel]:
        """Synchronous wrapper for suggest_schema(). Jupyter-compatible."""
        return _run_sync(self.suggest_schema(source, **kwargs))  # type: ignore[no-any-return]

    async def suggest_schema_source(
        self,
        source: str,
        *,
        sheet: str | None = None,
        instructions: str | None = None,
        **storage_options: Any,
    ) -> str:
        """Analyze an Excel file and return a suggested Pydantic schema as source code.

        Combines ``suggest_schema()`` with source code rendering to produce
        a ready-to-use Python module string containing the model class.

        Args:
            source: File path or URL (local, s3://, az://, gs://).
            sheet: Target sheet name. None = first sheet.
            instructions: Hints (e.g. "focus on financial columns").
            **storage_options: Backend-specific storage options.

        Returns:
            Python source code string defining a Pydantic model class with
            imports, field definitions, and descriptions.
        """
        from xlstruct.suggest import render_schema_source

        model_cls = await self.suggest_schema(
            source, sheet=sheet, instructions=instructions, **storage_options
        )
        return render_schema_source(model_cls)

    def suggest_schema_source_sync(
        self,
        source: str,
        **kwargs: Any,
    ) -> str:
        """Synchronous wrapper for suggest_schema_source(). Jupyter-compatible."""
        return _run_sync(self.suggest_schema_source(source, **kwargs))  # type: ignore[no-any-return]

    # * Multi-sheet extraction

    async def extract_workbook(
        self,
        source: str,
        sheet_schemas: dict[str, type[BaseModel]],
        *,
        concurrency: int = 5,
        instructions: str | None = None,
        on_progress: Callable[[ProgressEvent], None] | None = None,
        **storage_options: Any,
    ) -> WorkbookResult:
        """Extract structured data from multiple sheets in a single workbook.

        Each sheet is mapped to its own Pydantic schema and extracted in parallel.
        Individual sheet failures do not stop the workbook — partial results are returned.

        Args:
            source: File path or URL.
            sheet_schemas: Mapping of sheet name → Pydantic model class.
            concurrency: Max sheets processed simultaneously (default 5).
            instructions: Optional natural-language hints for the LLM.
            on_progress: Optional callback invoked after each sheet completes.
            **storage_options: Backend-specific storage options.

        Returns:
            WorkbookResult with per-sheet results keyed by sheet name.
        """
        # ^ Load all sheets at once (sheet_name=None)
        workbook = await self._load_workbook(source, sheet_name=None, **storage_options)

        semaphore = asyncio.Semaphore(concurrency)
        total = len(sheet_schemas)
        completed_count = 0
        count_lock = asyncio.Lock()

        async def _extract_sheet(
            sheet_name: str, schema: type[BaseModel]
        ) -> tuple[str, SheetResult[Any]]:
            nonlocal completed_count

            if on_progress:
                on_progress(
                    ProgressEvent(
                        source=sheet_name,
                        status=ProgressStatus.STARTED,
                        completed=completed_count,
                        total=total,
                    )
                )

            async with semaphore:
                sheet_data = workbook.get_sheet(sheet_name)
                if sheet_data is None:
                    error_msg = f"Sheet '{sheet_name}' not found. Available: {workbook.sheet_names}"
                    sheet_result: SheetResult[Any] = SheetResult(
                        sheet_name=sheet_name,
                        success=False,
                        error=error_msg,
                    )
                    status = ProgressStatus.FAILED
                else:
                    try:
                        tracker = UsageTracker()
                        engine = ExtractionEngine(self._config, tracker=tracker)
                        items = await self._run_sheet_extraction(
                            sheet_data, schema, instructions, engine=engine
                        )
                        sheet_result = SheetResult(
                            sheet_name=sheet_name,
                            success=True,
                            records=items,
                            usage=tracker.snapshot(),
                        )
                        status = ProgressStatus.COMPLETED
                        error_msg = None
                    except Exception as e:
                        logger.warning(
                            "Workbook extraction failed for sheet '%s': %s", sheet_name, e
                        )
                        error_msg = f"{type(e).__name__}: {e}"
                        sheet_result = SheetResult(
                            sheet_name=sheet_name,
                            success=False,
                            error=error_msg,
                        )
                        status = ProgressStatus.FAILED

            async with count_lock:
                completed_count += 1
                current_completed = completed_count

            if on_progress:
                on_progress(
                    ProgressEvent(
                        source=sheet_name,
                        status=status,
                        completed=current_completed,
                        total=total,
                        error=error_msg,
                    )
                )

            return sheet_name, sheet_result

        pairs = await asyncio.gather(
            *[_extract_sheet(name, schema) for name, schema in sheet_schemas.items()]
        )
        return WorkbookResult(results=dict(pairs))

    def extract_workbook_sync(
        self,
        source: str,
        sheet_schemas: dict[str, type[BaseModel]],
        **kwargs: Any,
    ) -> WorkbookResult:
        """Synchronous wrapper for extract_workbook(). Jupyter-compatible."""
        return _run_sync(  # type: ignore[no-any-return]
            self.extract_workbook(source, sheet_schemas, **kwargs)
        )

    # * Batch extraction

    async def extract_batch(
        self,
        sources: list[str],
        schema: type[T] | None = None,
        *,
        extraction_config: ExtractionConfig | None = None,
        concurrency: int = 5,
        sheet: str | None = None,
        instructions: str | None = None,
        on_progress: Callable[[ProgressEvent], None] | None = None,
        **storage_options: Any,
    ) -> BatchResult[T]:
        """Extract structured data from multiple files in parallel.

        Processes files concurrently with a configurable concurrency limit.
        Individual file failures do not stop the batch — partial results are returned.

        Args:
            sources: List of file paths or URLs.
            schema: Pydantic model class defining the target structure.
            extraction_config: Per-extraction config (applied to all files).
            concurrency: Max number of files processed simultaneously (default 5).
            sheet: Target sheet name (applied to all files).
            instructions: Optional natural-language hints for the LLM.
            on_progress: Optional callback invoked after each file completes.
            **storage_options: Backend-specific storage options.

        Returns:
            BatchResult with per-file results and aggregated usage.
        """
        semaphore = asyncio.Semaphore(concurrency)
        total = len(sources)
        completed_count = 0
        count_lock = asyncio.Lock()

        async def _process_one(source: str) -> FileResult[T]:
            nonlocal completed_count

            if on_progress:
                on_progress(
                    ProgressEvent(
                        source=source,
                        status=ProgressStatus.STARTED,
                        completed=completed_count,
                        total=total,
                    )
                )

            async with semaphore:
                try:
                    result = await self.extract(
                        source,
                        schema,
                        extraction_config=extraction_config,
                        sheet=sheet,
                        instructions=instructions,
                        **storage_options,
                    )
                    file_result = FileResult(
                        source=source,
                        success=True,
                        records=list(result),
                        usage=result.report.usage,
                    )
                    status = ProgressStatus.COMPLETED
                    error_msg = None
                except Exception as e:
                    logger.warning("Batch extraction failed for %s: %s", source, e)
                    error_msg = f"{type(e).__name__}: {e}"
                    file_result = FileResult(
                        source=source,
                        success=False,
                        error=error_msg,
                    )
                    status = ProgressStatus.FAILED

            async with count_lock:
                completed_count += 1
                current_completed = completed_count

            if on_progress:
                on_progress(
                    ProgressEvent(
                        source=source,
                        status=status,
                        completed=current_completed,
                        total=total,
                        error=error_msg,
                    )
                )

            return file_result

        file_results = await asyncio.gather(*[_process_one(s) for s in sources])
        return BatchResult(results=list(file_results))

    def extract_batch_sync(
        self,
        sources: list[str],
        schema: type[T] | None = None,
        **kwargs: Any,
    ) -> BatchResult[T]:
        """Synchronous wrapper for extract_batch(). Jupyter-compatible."""
        return _run_sync(  # type: ignore[no-any-return]
            self.extract_batch(sources, schema, **kwargs)
        )

    # * Private pipeline methods

    @staticmethod
    def _get_source_ext(source: str) -> str:
        """Extract and validate file extension from source path/URL."""
        lower = source.lower().rsplit("?", 1)[0]  # ^ Strip query params for URLs
        for ext in (".xlsm", ".xltx", ".xltm", ".xlsx", ".xls", ".csv"):
            if lower.endswith(ext):
                return ext
        raise ReaderError(
            f"Unsupported file format: {source}",
            code=ErrorCode.READER_UNSUPPORTED_FORMAT,
        )

    async def _load_workbook(
        self,
        source: str,
        sheet_name: str | None = None,
        **storage_options: Any,
    ) -> WorkbookData:
        """Storage → Reader pipeline."""
        merged_options = {**self._config.storage_options, **storage_options}
        file_bytes = await read_file(source, **merged_options)

        ext = self._get_source_ext(source)

        if ext == ".csv":
            from xlstruct.reader.csv_reader import CsvReader

            csv_reader = CsvReader()
            workbook = await asyncio.to_thread(csv_reader.read, file_bytes, sheet_name)
        else:
            reader = HybridReader()
            workbook = await asyncio.to_thread(
                reader.read,
                file_bytes,
                sheet_name,
                source_ext=ext,
                strict_formulas=self._config.strict_formulas,
                evaluate_formulas=self._config.evaluate_formulas,
            )

        workbook.file_name = source.rsplit("/", 1)[-1]
        workbook.file_size = len(file_bytes)
        return workbook

    async def _run_configured_extraction(
        self,
        source: str,
        config: ExtractionConfig,
        **storage_options: Any,
    ) -> tuple[list[Any], ExtractionMode]:
        """Config-based extraction with mode selection.

        - mode=auto: heuristic routing (≤ SAMPLE_ROWS → direct, > SAMPLE_ROWS → codegen).
        - mode=direct: always use LLM direct extraction.
        - mode=codegen: always use code generation pipeline.

        Returns:
            Tuple of (extracted items, resolved extraction mode).
        """
        workbook = await self._load_workbook(source, sheet_name=config.sheet, **storage_options)
        full_sheet = workbook.sheets[0]
        codegen = self._get_codegen()

        # * Auto-detect header rows if not provided
        header_rows = config.header_rows
        if header_rows is None:
            header_rows = await codegen.detect_header_rows(full_sheet)

        # * Resolve mode
        mode = config.mode
        if mode == ExtractionMode.AUTO:
            max_header_row = max(header_rows)
            data_rows = full_sheet.row_count - max_header_row
            if data_rows > SAMPLE_ROWS:
                mode = ExtractionMode.CODEGEN
            else:
                mode = ExtractionMode.DIRECT
            logger.info(
                "Auto-routing: %d data rows → mode=%s",
                data_rows,
                mode.value,
            )

        if mode == ExtractionMode.CODEGEN:
            items = await self._run_codegen(source, full_sheet, header_rows, config, codegen)
            return items, ExtractionMode.CODEGEN

        items = await self._run_direct(full_sheet, header_rows, config)
        return items, ExtractionMode.DIRECT

    async def _run_codegen(
        self,
        source: str,
        full_sheet: SheetData,
        header_rows: list[int],
        config: ExtractionConfig,
        codegen: CodegenOrchestrator,
    ) -> list[Any]:
        """Code generation pipeline: cache lookup → generate script → execute → parse."""
        script: GeneratedScript | None = None

        # * Cache lookup
        if self._cache is not None:
            signature = compute_structure_signature(full_sheet, header_rows, config.output_schema)
            script = self._cache.get(signature)

        if script is None:
            # * Cache miss — generate via LLM
            script = await codegen.generate_script(source, full_sheet, header_rows, config)
            self._export_script(source, script)

            # * Store in cache
            if self._cache is not None:
                self._cache.put(signature, script, full_sheet, header_rows, config.output_schema)

        return await codegen.run_extraction(source, script, config.output_schema)

    async def _run_direct(
        self,
        full_sheet: SheetData,
        header_rows: list[int],
        config: ExtractionConfig,
    ) -> list[Any]:
        """Direct LLM extraction: encode → LLM → Pydantic."""
        encoder = CompressedEncoder(sample_size=SAMPLE_ROWS)
        encoded = encoder.encode(full_sheet, header_rows=header_rows)

        return await self._engine.extract(
            encoded,
            config.output_schema,
            config.instructions,
            is_sampled=True,
            total_rows=full_sheet.row_count,
            track_provenance=config.track_provenance,
            include_confidence=config.include_confidence,
        )

    async def _run_sheet_extraction(
        self,
        sheet: SheetData,
        schema: type[T],
        instructions: str | None = None,
        *,
        engine: ExtractionEngine,
    ) -> list[T]:
        """Encoder → (optional Chunking) → ExtractionEngine pipeline."""
        target_engine = engine
        encoder = CompressedEncoder()

        if needs_chunking(sheet, self._config.token_budget):
            # * Chunked extraction
            chunks = self._chunk_splitter.split(sheet, self._config.token_budget)
            all_results: list[T] = []
            for chunk in chunks:
                encoded = encoder.encode(chunk)
                partial = await target_engine.extract(encoded, schema, instructions)
                all_results.extend(partial)
            return all_results
        else:
            # * Single-pass extraction
            encoded = encoder.encode(sheet)
            return await target_engine.extract(encoded, schema, instructions)
