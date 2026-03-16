"""Extractor: Public API for XLStruct.

Orchestrates the full pipeline: Storage → Reader → Encoder → Engine.
Delegates code generation to CodegenOrchestrator.
"""

import asyncio
import logging
import re
from pathlib import Path as PathLibPath
from typing import Any, TypeVar

from pydantic import BaseModel, SecretStr

from xlstruct.codegen.backends.base import ExecutionBackend
from xlstruct.codegen.cache import ScriptCache, compute_structure_signature
from xlstruct.codegen.orchestrator import CodegenOrchestrator
from xlstruct.config import SAMPLE_ROWS, ExtractionConfig, ExtractionMode, ExtractorConfig
from xlstruct.encoder.compressed import CompressedEncoder
from xlstruct.exceptions import ReaderError
from xlstruct.extraction.chunking import ChunkSplitter, needs_chunking
from xlstruct.extraction.engine import ExtractionEngine
from xlstruct.reader.hybrid_reader import HybridReader
from xlstruct.schemas.batch import BatchResult, FileResult
from xlstruct.schemas.codegen import GeneratedScript
from xlstruct.schemas.core import SheetData, WorkbookData
from xlstruct.schemas.usage import TokenUsage, UsageTracker
from xlstruct.storage import read_file

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class ExtractionResult(list[T]):  # type: ignore[type-var,unused-ignore]
    """List of extracted records with attached token usage info.

    Behaves exactly like list[T] (iteration, indexing, len, etc.)
    but also exposes a `.usage` attribute with token consumption details.
    """

    usage: TokenUsage

    def __init__(self, items: list[T], usage: TokenUsage) -> None:
        super().__init__(items)
        self.usage = usage


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
            ExtractionResult — list[T] with `.usage` attribute for token consumption.
        """
        self._tracker.reset()

        if extraction_config is not None:
            items = await self._run_configured_extraction(
                source, extraction_config, **storage_options
            )
        elif schema is not None:
            workbook = await self._load_workbook(source, sheet_name=sheet, **storage_options)
            target_sheet = workbook.sheets[0]
            items = await self._run_sheet_extraction(target_sheet, schema, instructions)
        else:
            raise ValueError("Either schema or extraction_config must be provided")

        usage = self._tracker.snapshot()
        logger.info(usage)
        return ExtractionResult(items, usage=usage)

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

        script = await codegen.generate_script(
            source, full_sheet, header_rows, extraction_config
        )
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

        import instructor

        from xlstruct.config import apply_cache_control, get_provider_kwargs
        from xlstruct.prompts.system import SYSTEM_PROMPT
        from xlstruct.schemas.suggest import SuggestedFields

        kwargs = get_provider_kwargs(self._config)
        if self._config.api_key:
            kwargs["api_key"] = self._config.api_key.get_secret_value()

        client = instructor.from_provider(
            self._config.provider,
            async_client=True,
            **kwargs,
        )

        messages = apply_cache_control(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            self._config.provider,
        )
        result, completion = await client.create_with_completion(
            response_model=SuggestedFields,
            messages=messages,  # type: ignore[arg-type]
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
            **storage_options: Backend-specific storage options.

        Returns:
            BatchResult with per-file results and aggregated usage.
        """
        semaphore = asyncio.Semaphore(concurrency)

        async def _process_one(source: str) -> FileResult[T]:
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
                    return FileResult(
                        source=source,
                        success=True,
                        records=list(result),
                        usage=result.usage,
                    )
                except Exception as e:
                    logger.warning("Batch extraction failed for %s: %s", source, e)
                    return FileResult(
                        source=source,
                        success=False,
                        error=f"{type(e).__name__}: {e}",
                    )

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
        raise ReaderError(f"Unsupported file format: {source}")

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
            workbook = await asyncio.to_thread(
                csv_reader.read, file_bytes, sheet_name
            )
        else:
            reader = HybridReader()
            workbook = await asyncio.to_thread(
                reader.read, file_bytes, sheet_name, source_ext=ext
            )

        workbook.file_name = source.rsplit("/", 1)[-1]
        workbook.file_size = len(file_bytes)
        return workbook

    async def _run_configured_extraction(
        self,
        source: str,
        config: ExtractionConfig,
        **storage_options: Any,
    ) -> list[Any]:
        """Config-based extraction with mode selection.

        - mode=auto: heuristic routing (≤ SAMPLE_ROWS → direct, > SAMPLE_ROWS → codegen).
        - mode=direct: always use LLM direct extraction.
        - mode=codegen: always use code generation pipeline.
        """
        workbook = await self._load_workbook(
            source, sheet_name=config.sheet, **storage_options
        )
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
                data_rows, mode.value,
            )

        if mode == ExtractionMode.CODEGEN:
            return await self._run_codegen(source, full_sheet, header_rows, config, codegen)

        return await self._run_direct(full_sheet, header_rows, config)

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
            signature = compute_structure_signature(
                full_sheet, header_rows, config.output_schema
            )
            script = self._cache.get(signature)

        if script is None:
            # * Cache miss — generate via LLM
            script = await codegen.generate_script(
                source, full_sheet, header_rows, config
            )
            self._export_script(source, script)

            # * Store in cache
            if self._cache is not None:
                self._cache.put(
                    signature, script, full_sheet, header_rows, config.output_schema
                )

        return await codegen.run_extraction(
            source, script, config.output_schema
        )

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
        )

    async def _run_sheet_extraction(
        self,
        sheet: SheetData,
        schema: type[T],
        instructions: str | None = None,
    ) -> list[T]:
        """Encoder → (optional Chunking) → ExtractionEngine pipeline."""
        encoder = CompressedEncoder()

        if needs_chunking(sheet, self._config.token_budget):
            # * Chunked extraction
            chunks = self._chunk_splitter.split(sheet, self._config.token_budget)
            all_results: list[T] = []
            for chunk in chunks:
                encoded = encoder.encode(chunk)
                partial = await self._engine.extract(encoded, schema, instructions)
                all_results.extend(partial)
            return all_results
        else:
            # * Single-pass extraction
            encoded = encoder.encode(sheet)
            return await self._engine.extract(encoded, schema, instructions)
