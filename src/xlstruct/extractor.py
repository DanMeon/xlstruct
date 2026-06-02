"""Extractor: public API facade for XLStruct.

Thin facade over :class:`ExtractionPipeline`. It builds the config, owns a single
pipeline instance, and delegates every operation to it — all orchestration
(Storage → Reader → Encoder → Engine, codegen routing, chunking, concurrency)
lives in the pipeline.
"""

import asyncio
from collections.abc import AsyncGenerator, Callable, Iterator
from typing import Any, TypeVar

from pydantic import BaseModel, SecretStr

from xlstruct.codegen.backends.base import ExecutionBackend
from xlstruct.codegen.cache import ScriptCache
from xlstruct.config import ExtractionConfig, ExtractorConfig
from xlstruct.extraction.chunking import ChunkSplitter
from xlstruct.extraction.engine import ExtractionEngine
from xlstruct.extraction.pipeline import ExtractionPipeline
from xlstruct.extraction.result import ExtractionResult
from xlstruct.reader.dispatch import get_source_ext
from xlstruct.schemas.batch import BatchResult
from xlstruct.schemas.codegen import GeneratedScript
from xlstruct.schemas.core import SheetData, WorkbookData
from xlstruct.schemas.progress import ProgressEvent
from xlstruct.schemas.workbook import WorkbookResult

T = TypeVar("T", bound=BaseModel)


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
        import nest_asyncio  # type: ignore

        nest_asyncio.apply()  # type: ignore
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
            resolved_config = config
        else:
            secret_key = SecretStr(api_key) if api_key is not None else None
            resolved_config = ExtractorConfig(provider=provider, api_key=secret_key, **kwargs)

        self._pipeline = ExtractionPipeline(resolved_config, execution_backend=execution_backend)

    # * Shared internals — the pipeline owns these; exposed for introspection and tests

    @property
    def _config(self) -> ExtractorConfig:
        return self._pipeline.config

    @property
    def _engine(self) -> ExtractionEngine:
        return self._pipeline.engine

    @property
    def _chunk_splitter(self) -> ChunkSplitter:
        return self._pipeline.chunk_splitter

    @property
    def cache(self) -> ScriptCache | None:
        """Access the script cache for codegen mode.

        Returns None if caching is disabled (``cache_enabled=False``).
        When enabled, provides ``list_entries()``, ``clear()``, ``remove()`` methods.
        """
        return self._pipeline.cache

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
        return await self._pipeline.extract(
            source,
            schema,
            extraction_config=extraction_config,
            sheet=sheet,
            instructions=instructions,
            **storage_options,
        )

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
        return await self._pipeline.generate_script(source, extraction_config, **storage_options)

    def generate_script_sync(
        self,
        source: str,
        extraction_config: ExtractionConfig,
        **storage_options: Any,
    ) -> GeneratedScript:
        """Synchronous wrapper for generate_script(). Jupyter-compatible."""
        return _run_sync(  # type: ignore
            self.generate_script(source, extraction_config, **storage_options)
        )

    def extract_sync(
        self,
        source: str,
        schema: type[T] | None = None,
        **kwargs: Any,
    ) -> ExtractionResult[T]:
        """Synchronous wrapper for extract(). Jupyter-compatible."""
        return _run_sync(self.extract(source, schema, **kwargs))  # type: ignore

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
        """Stream extracted records as each chunk completes.

        Same pipeline as ``extract()`` but yields individual records incrementally
        instead of waiting for all chunks to finish. For large multi-chunk files
        this means the caller receives records as soon as each chunk's LLM call
        completes.

        For codegen mode and single-chunk extractions, all records are yielded
        at once (no partial streaming benefit, but the interface is consistent).

        Args:
            source: File path or URL (local, s3://, az://, gs://).
            schema: (Legacy) Pydantic model class defining the target structure.
            extraction_config: Per-extraction config with header_rows, output_schema.
            sheet: Target sheet name. None = first sheet.
            instructions: Optional natural-language hints for the LLM.
            **storage_options: Backend-specific storage options.

        Yields:
            Individual ``T`` instances as they are extracted.
        """
        async for item in self._pipeline.stream(
            source,
            schema,
            extraction_config=extraction_config,
            sheet=sheet,
            instructions=instructions,
            **storage_options,
        ):
            yield item

    def stream_sync(
        self,
        source: str,
        schema: type[T] | None = None,
        **kwargs: Any,
    ) -> Iterator[T]:
        """Synchronous wrapper for stream(). Jupyter-compatible.

        Returns a regular ``Iterator[T]`` by collecting all records from the
        async generator. This does not provide true incremental streaming to
        the caller (all records are collected before returning), but it
        preserves the same interface contract.

        For true incremental streaming, use ``async for item in extractor.stream(...)``.
        """

        async def _collect() -> list[T]:
            results: list[T] = []
            async for item in self.stream(source, schema, **kwargs):
                results.append(item)
            return results

        return iter(_run_sync(_collect()))

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
        return await self._pipeline.suggest_schema(
            source, sheet=sheet, instructions=instructions, **storage_options
        )

    def suggest_schema_sync(
        self,
        source: str,
        **kwargs: Any,
    ) -> type[BaseModel]:
        """Synchronous wrapper for suggest_schema(). Jupyter-compatible."""
        return _run_sync(self.suggest_schema(source, **kwargs))  # type: ignore

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
        return _run_sync(self.suggest_schema_source(source, **kwargs))  # type: ignore

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
        return await self._pipeline.extract_workbook(
            source,
            sheet_schemas,
            concurrency=concurrency,
            instructions=instructions,
            on_progress=on_progress,
            **storage_options,
        )

    def extract_workbook_sync(
        self,
        source: str,
        sheet_schemas: dict[str, type[BaseModel]],
        **kwargs: Any,
    ) -> WorkbookResult:
        """Synchronous wrapper for extract_workbook(). Jupyter-compatible."""
        return _run_sync(  # type: ignore
            self.extract_workbook(source, sheet_schemas, **kwargs)
        )

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
        """Extract structured data by combining multiple sheets into a single LLM call.

        Unlike extract_workbook (one schema per sheet), this method encodes multiple
        sheets and sends the combined representation to a single LLM extraction call,
        producing a unified list of records. Useful when related data is split across
        sheets (e.g. Q1/Q2/Q3 tabs) and must be merged into one schema.

        Args:
            source: File path or URL (local, s3://, az://, gs://).
            schema: Pydantic model class defining the target structure.
            sheets: Sheet names to include (minimum 2).
            header_rows: Header row specification. Can be:
                - None: auto-detect per sheet.
                - list[int]: same header rows applied to all sheets.
                - dict[str, list[int]]: per-sheet header rows keyed by sheet name.
            instructions: Optional natural-language hints for the LLM.
            **storage_options: Backend-specific storage options.

        Returns:
            ExtractionResult — list[T] with ``.report`` for extraction metadata.

        Raises:
            ValueError: If fewer than 2 sheets are specified or a sheet is not found.
        """
        return await self._pipeline.extract_cross_sheet(
            source,
            schema=schema,
            sheets=sheets,
            header_rows=header_rows,
            instructions=instructions,
            **storage_options,
        )

    def extract_cross_sheet_sync(
        self,
        source: str,
        *,
        schema: type[T],
        sheets: list[str],
        **kwargs: Any,
    ) -> ExtractionResult[T]:
        """Synchronous wrapper for extract_cross_sheet(). Jupyter-compatible."""
        return _run_sync(  # type: ignore
            self.extract_cross_sheet(source, schema=schema, sheets=sheets, **kwargs)
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
        return await self._pipeline.extract_batch(
            sources,
            schema,
            extraction_config=extraction_config,
            concurrency=concurrency,
            sheet=sheet,
            instructions=instructions,
            on_progress=on_progress,
            **storage_options,
        )

    def extract_batch_sync(
        self,
        sources: list[str],
        schema: type[T] | None = None,
        **kwargs: Any,
    ) -> BatchResult[T]:
        """Synchronous wrapper for extract_batch(). Jupyter-compatible."""
        return _run_sync(  # type: ignore
            self.extract_batch(sources, schema, **kwargs)
        )

    # * Private delegators — kept on the facade for direct introspection/tests

    @staticmethod
    def _get_source_ext(source: str) -> str:
        """Extract and validate file extension from source path/URL."""
        return get_source_ext(source)

    async def _load_workbook(
        self,
        source: str,
        sheet_name: str | None = None,
        **storage_options: Any,
    ) -> WorkbookData:
        """Storage → Reader pipeline (delegates to ExtractionPipeline)."""
        return await self._pipeline.load_workbook(source, sheet_name=sheet_name, **storage_options)

    async def _run_sheet_extraction(
        self,
        sheet: SheetData,
        schema: type[T],
        instructions: str | None = None,
        *,
        engine: ExtractionEngine,
    ) -> list[T]:
        """Encoder → (optional Chunking) → ExtractionEngine pipeline (delegates)."""
        return await self._pipeline.run_sheet_extraction(sheet, schema, instructions, engine=engine)
