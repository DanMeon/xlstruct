"""ExtractionPipeline: orchestration engine behind the Extractor facade.

Owns the Storage → Reader → Encoder → Engine pipeline plus codegen routing,
chunked and streaming execution, and multi-sheet/batch concurrency. ``Extractor``
is a thin public facade that delegates every operation here.
"""

import asyncio
import logging
import re
from collections.abc import AsyncGenerator, Callable
from pathlib import Path as PathLibPath
from typing import Any, TypeVar

from pydantic import BaseModel

from xlstruct._tokens import count_tokens
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
    thinking_call_kwargs,
)
from xlstruct.encoder.compressed import CompressedEncoder
from xlstruct.exceptions import ErrorCode, ExtractionError, ReaderError
from xlstruct.extraction.chunking import ChunkSplitter, needs_chunking
from xlstruct.extraction.concurrency import run_concurrent
from xlstruct.extraction.engine import ExtractionEngine
from xlstruct.extraction.report import build_extraction_report
from xlstruct.extraction.result import ExtractionResult
from xlstruct.reader.base import ReaderOptions
from xlstruct.reader.dispatch import read_workbook
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


class ExtractionPipeline:
    """Orchestration engine for XLStruct. Public surface lives on ``Extractor``."""

    def __init__(
        self,
        config: ExtractorConfig,
        *,
        execution_backend: ExecutionBackend | None = None,
    ) -> None:
        self._config = config
        self._execution_backend = execution_backend
        self._tracker = UsageTracker()
        self._engine = ExtractionEngine(self._config, tracker=self._tracker)
        self._codegen: CodegenOrchestrator | None = None
        self._chunk_splitter = ChunkSplitter()
        self._cache: ScriptCache | None = None
        if self._config.cache_enabled:
            self._cache = ScriptCache(cache_dir=self._config.cache_dir)

    # * Public read-only state — consumed by the Extractor facade

    @property
    def config(self) -> ExtractorConfig:
        return self._config

    @property
    def engine(self) -> ExtractionEngine:
        return self._engine

    @property
    def chunk_splitter(self) -> ChunkSplitter:
        return self._chunk_splitter

    @property
    def cache(self) -> ScriptCache | None:
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

    # * Single-sheet extraction

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
        """Implementation of ``Extractor.extract()``."""
        self._tracker.reset()

        if extraction_config is not None:
            items, resolved_mode = await self._run_configured_extraction(
                source, extraction_config, **storage_options
            )
        elif schema is not None:
            workbook = await self.load_workbook(source, sheet_name=sheet, **storage_options)
            target_sheet = workbook.sheets[0]
            self._require_non_empty_sheet(target_sheet)
            items = await self.run_sheet_extraction(
                target_sheet, schema, instructions, engine=self._engine
            )
            resolved_mode = ExtractionMode.DIRECT
        else:
            raise ValueError("Either schema or extraction_config must be provided")

        usage = self._tracker.snapshot()
        logger.info(usage)

        report = build_extraction_report(items, resolved_mode, usage)
        return ExtractionResult(items, report=report)

    async def generate_script(
        self,
        source: str,
        extraction_config: ExtractionConfig,
        **storage_options: Any,
    ) -> GeneratedScript:
        """Implementation of ``Extractor.generate_script()``."""
        workbook = await self.load_workbook(
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

    # * Streaming extraction

    async def stream(
        self,
        source: str,
        schema: type[T] | None = None,
        *,
        extraction_config: ExtractionConfig | None = None,
        sheet: str | None = None,
        instructions: str | None = None,
        **storage_options: Any,
    ) -> AsyncGenerator[T, None]:
        """Implementation of ``Extractor.stream()``."""
        if extraction_config is not None:
            async for item in self._stream_configured_extraction(
                source, extraction_config, **storage_options
            ):
                yield item
        elif schema is not None:
            workbook = await self.load_workbook(source, sheet_name=sheet, **storage_options)
            target_sheet = workbook.sheets[0]
            async for item in self._stream_sheet_extraction(
                target_sheet, schema, instructions, engine=self._engine
            ):
                yield item
        else:
            raise ValueError("Either schema or extraction_config must be provided")

    async def suggest_schema(
        self,
        source: str,
        *,
        sheet: str | None = None,
        instructions: str | None = None,
        **storage_options: Any,
    ) -> type[BaseModel]:
        """Implementation of ``Extractor.suggest_schema()``."""
        from pydantic import Field, create_model

        workbook = await self.load_workbook(source, sheet_name=sheet, **storage_options)
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
        call_kwargs: dict[str, Any] = {"temperature": 0.0, **thinking_call_kwargs(self._config)}

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
            **call_kwargs,
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
                python_type = python_type | None  # type: ignore
            field_definitions[f.name] = (
                python_type,
                Field(description=f.description),
            )

        return create_model(result.model_name, **field_definitions)

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
        """Implementation of ``Extractor.extract_workbook()``."""
        # ^ Load all sheets at once (sheet_name=None)
        workbook = await self.load_workbook(source, sheet_name=None, **storage_options)

        async def _worker(
            pair: tuple[str, type[BaseModel]],
        ) -> tuple[tuple[str, SheetResult[Any]], ProgressStatus, str | None]:
            sheet_name, schema = pair
            sheet_data = workbook.get_sheet(sheet_name)
            if sheet_data is None:
                error_msg = f"Sheet '{sheet_name}' not found. Available: {workbook.sheet_names}"
                result: SheetResult[Any] = SheetResult(
                    sheet_name=sheet_name, success=False, error=error_msg
                )
                return (sheet_name, result), ProgressStatus.FAILED, error_msg

            try:
                tracker = UsageTracker()
                engine = ExtractionEngine(self._config, tracker=tracker)
                items = await self.run_sheet_extraction(
                    sheet_data, schema, instructions, engine=engine
                )
                result = SheetResult(
                    sheet_name=sheet_name,
                    success=True,
                    records=items,
                    usage=tracker.snapshot(),
                )
                return (sheet_name, result), ProgressStatus.COMPLETED, None
            except Exception as e:
                logger.warning("Workbook extraction failed for sheet '%s': %s", sheet_name, e)
                error_msg = f"{type(e).__name__}: {e}"
                result = SheetResult(sheet_name=sheet_name, success=False, error=error_msg)
                return (sheet_name, result), ProgressStatus.FAILED, error_msg

        pairs = await run_concurrent(
            list(sheet_schemas.items()),
            _worker,
            concurrency=concurrency,
            label=lambda pair: pair[0],
            on_progress=on_progress,
        )
        return WorkbookResult(results=dict(pairs))

    # * Cross-sheet extraction

    async def extract_cross_sheet(
        self,
        source: str,
        *,
        schema: type[T],
        sheets: list[str],
        header_rows: dict[str, list[int]] | list[int] | None = None,
        instructions: str | None = None,
        **storage_options: Any,
    ) -> ExtractionResult[T]:
        """Implementation of ``Extractor.extract_cross_sheet()``."""
        if len(sheets) < 2:
            raise ValueError(f"extract_cross_sheet requires at least 2 sheets, got {len(sheets)}")

        self._tracker.reset()

        # * Load entire workbook (all sheets)
        workbook = await self.load_workbook(source, sheet_name=None, **storage_options)

        # * Validate all requested sheets exist
        missing = [s for s in sheets if workbook.get_sheet(s) is None]
        if missing:
            raise ValueError(f"Sheets not found: {missing}. Available: {workbook.sheet_names}")

        # * Encode each sheet separately and concatenate
        encoder = CompressedEncoder(sample_size=SAMPLE_ROWS)
        encoded_parts: list[str] = []
        for sheet_name in sheets:
            sheet_data = workbook.get_sheet(sheet_name)
            assert sheet_data is not None  # ^ Already validated above

            # * Resolve header_rows for this sheet
            sheet_header_rows: list[int] | None
            if header_rows is None:
                sheet_header_rows = None
            elif isinstance(header_rows, list):
                sheet_header_rows = header_rows
            else:
                sheet_header_rows = header_rows.get(sheet_name)

            encoded_parts.append(encoder.encode(sheet_data, header_rows=sheet_header_rows))

        combined_encoding = "\n\n".join(encoded_parts)

        # * Validate combined encoding fits within token budget
        combined_tokens = count_tokens(combined_encoding)
        if combined_tokens > self._config.token_budget:
            raise ExtractionError(
                f"Combined cross-sheet encoding ({combined_tokens:,} tokens) exceeds "
                f"token budget ({self._config.token_budget:,}). "
                f"Reduce the number of sheets or increase token_budget.",
                code=ErrorCode.EXTRACTION_LLM_FAILED,
            )

        # * Send combined encoding to ExtractionEngine
        items = await self._engine.extract(
            combined_encoding,
            schema,
            instructions,
        )

        usage = self._tracker.snapshot()
        logger.info(usage)

        report = ExtractionReport(
            mode=ExtractionMode.DIRECT,
            usage=usage,
        )
        return ExtractionResult(items, report=report)

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
        """Implementation of ``Extractor.extract_batch()``."""

        async def _worker(source: str) -> tuple[FileResult[T], ProgressStatus, str | None]:
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
                return file_result, ProgressStatus.COMPLETED, None
            except Exception as e:
                logger.warning("Batch extraction failed for %s: %s", source, e)
                error_msg = f"{type(e).__name__}: {e}"
                return (
                    FileResult[T](source=source, success=False, error=error_msg),
                    ProgressStatus.FAILED,
                    error_msg,
                )

        file_results = await run_concurrent(
            sources,
            _worker,
            concurrency=concurrency,
            label=lambda source: source,
            on_progress=on_progress,
        )
        return BatchResult(results=list(file_results))

    # * Private pipeline methods

    @staticmethod
    def _require_non_empty_sheet(sheet: SheetData) -> None:
        """Fail fast on a 0-row sheet before spending an LLM header-detection call."""
        if sheet.row_count == 0:
            raise ReaderError(
                f"Sheet '{sheet.name}' has no rows.",
                code=ErrorCode.READER_PARSE_FAILED,
            )

    async def load_workbook(
        self,
        source: str,
        sheet_name: str | None = None,
        **storage_options: Any,
    ) -> WorkbookData:
        """Storage → Reader pipeline."""
        merged_options = {**self._config.storage_options, **storage_options}
        file_bytes = await read_file(source, **merged_options)

        options = ReaderOptions(
            strict_formulas=self._config.strict_formulas,
            evaluate_formulas=self._config.evaluate_formulas,
            csv_encoding=self._config.csv_encoding,
        )
        workbook = await asyncio.to_thread(
            read_workbook, file_bytes, source, sheet_name, options=options
        )

        workbook.file_name = source.rsplit("/", 1)[-1]
        workbook.file_size = len(file_bytes)
        return workbook

    @staticmethod
    def _resolve_auto_mode(
        full_sheet: SheetData,
        header_rows: list[int],
        requested_mode: ExtractionMode,
        *,
        log_label: str = "Auto-routing",
    ) -> ExtractionMode:
        """Resolve AUTO to DIRECT/CODEGEN by data-row count; pass other modes through.

        Routing: data rows ≤ SAMPLE_ROWS → DIRECT, > SAMPLE_ROWS → CODEGEN.
        """
        if requested_mode != ExtractionMode.AUTO:
            return requested_mode
        data_rows = full_sheet.row_count - max(header_rows)
        mode = ExtractionMode.CODEGEN if data_rows > SAMPLE_ROWS else ExtractionMode.DIRECT
        logger.info("%s: %d data rows → mode=%s", log_label, data_rows, mode.value)
        return mode

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
        workbook = await self.load_workbook(source, sheet_name=config.sheet, **storage_options)
        full_sheet = workbook.sheets[0]
        self._require_non_empty_sheet(full_sheet)
        codegen = self._get_codegen()

        # * Auto-detect header rows if not provided
        header_rows = config.header_rows
        if header_rows is None:
            header_rows = await codegen.detect_header_rows(full_sheet)

        mode = self._resolve_auto_mode(full_sheet, header_rows, config.mode)

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
        signature: str | None = None

        # * Cache lookup
        if self._cache is not None:
            signature = compute_structure_signature(full_sheet, header_rows, config.output_schema)
            script = self._cache.get(signature)

        if script is None:
            # * Cache miss — generate via LLM
            script = await codegen.generate_script(source, full_sheet, header_rows, config)
            self._export_script(source, script)

            # * Store in cache
            if self._cache is not None and signature is not None:
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

    async def run_sheet_extraction(
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

        if needs_chunking(sheet, self._config.token_budget, self._config.chunking_row_threshold):
            # * Chunked extraction
            chunks = self._chunk_splitter.split(
                sheet,
                self._config.token_budget,
                min_chunk_rows=self._config.min_chunk_rows,
                row_threshold=self._config.chunking_row_threshold,
            )
            # * Parallel chunk extraction with bounded concurrency (rate-limit safe)
            semaphore = asyncio.Semaphore(self._config.max_concurrent_chunks)

            async def _extract_chunk(chunk: SheetData) -> list[T]:
                async with semaphore:
                    encoded = encoder.encode(chunk)
                    return await target_engine.extract(encoded, schema, instructions)

            # ^ gather preserves input order, so records stay in chunk order
            chunk_results = await asyncio.gather(*[_extract_chunk(c) for c in chunks])
            all_results: list[T] = []
            for partial in chunk_results:
                all_results.extend(partial)
            return all_results
        else:
            # * Single-pass extraction
            encoded = encoder.encode(sheet)
            return await target_engine.extract(encoded, schema, instructions)

    # * Streaming private helpers

    async def _stream_configured_extraction(
        self,
        source: str,
        config: ExtractionConfig,
        **storage_options: Any,
    ) -> AsyncGenerator[Any, None]:
        """Streaming variant of _run_configured_extraction.

        For codegen mode, yields all records at once (script produces all results
        in a single run). For direct mode, yields records as each chunk completes.
        """
        workbook = await self.load_workbook(source, sheet_name=config.sheet, **storage_options)
        full_sheet = workbook.sheets[0]
        self._require_non_empty_sheet(full_sheet)

        # * Auto-detect header rows if not provided (requires codegen orchestrator)
        header_rows = config.header_rows
        if header_rows is None:
            codegen = self._get_codegen()
            header_rows = await codegen.detect_header_rows(full_sheet)

        mode = self._resolve_auto_mode(
            full_sheet, header_rows, config.mode, log_label="Auto-routing (stream)"
        )

        if mode == ExtractionMode.CODEGEN:
            codegen = self._get_codegen()
            items = await self._run_codegen(source, full_sheet, header_rows, config, codegen)
            for item in items:
                yield item
            return

        # * Direct mode — single-pass with sampling (no chunking in config mode)
        encoder = CompressedEncoder(sample_size=SAMPLE_ROWS)
        encoded = encoder.encode(full_sheet, header_rows=header_rows)
        items = await self._engine.extract(
            encoded,
            config.output_schema,
            config.instructions,
            is_sampled=True,
            total_rows=full_sheet.row_count,
            track_provenance=config.track_provenance,
        )
        for item in items:
            yield item

    async def _stream_sheet_extraction(
        self,
        sheet: SheetData,
        schema: type[T],
        instructions: str | None = None,
        *,
        engine: ExtractionEngine,
    ) -> AsyncGenerator[T, None]:
        """Streaming variant of run_sheet_extraction.

        Yields records incrementally as each chunk's LLM call completes.
        For single-chunk sheets, yields all records at once.
        """
        encoder = CompressedEncoder()

        if needs_chunking(sheet, self._config.token_budget, self._config.chunking_row_threshold):
            # * Chunked extraction — yield from each chunk as it completes
            chunks = self._chunk_splitter.split(
                sheet,
                self._config.token_budget,
                min_chunk_rows=self._config.min_chunk_rows,
                row_threshold=self._config.chunking_row_threshold,
            )
            for chunk in chunks:
                encoded = encoder.encode(chunk)
                partial = await engine.extract(encoded, schema, instructions)
                for item in partial:
                    yield item
        else:
            # * Single-pass extraction
            encoded = encoder.encode(sheet)
            items = await engine.extract(encoded, schema, instructions)
            for item in items:
                yield item
