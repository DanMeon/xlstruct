# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-03-20

### Added

- **Progress tracking** — `on_progress` callback for `extract_batch()` and `extract_workbook()`
  - `ProgressEvent` and `ProgressStatus` models (`schemas/progress.py`)
  - Reports `STARTED`, `COMPLETED`, `FAILED` status with completed/total counts
  - Compatible with tqdm and custom progress UIs
- **Error code system** — machine-readable `ErrorCode` enum on all exceptions
  - 12 error codes: `STORAGE_NOT_FOUND`, `CODEGEN_MAX_RETRIES`, `EXTRACTION_LLM_FAILED`, etc.
  - `XLStructError.code` field for programmatic error handling (e.g. `match e.code`)
- **Public cache API** — `Extractor.cache` property exposes `ScriptCache` for inspection and management
- **Extraction report** — `ExtractionResult.report` with mode used, token usage, and optional row provenance
- **Row provenance** — `track_provenance=True` tracks source Excel row numbers per record
- **DataFrame export** — `ExtractionResult.to_dataframe()` converts results to pandas DataFrame
- **Multi-sheet extraction** — `extract_workbook()` extracts different schemas from different sheets in parallel

### Changed

- Centralized Instructor client creation via `build_instructor_client()` helper
- Unified CodegenEngine LLM calls through shared `_call_llm()` method

## [0.3.0] - 2026-03-16

### Added

- **Batch extraction** — `extract_batch()` / `extract_batch_sync()` for processing multiple files in parallel
  - `asyncio.Semaphore`-based concurrency control (default 5)
  - Partial failure support — individual file errors don't stop the batch
  - `BatchResult` / `FileResult` models with aggregated usage tracking and `all_records` accessor
- **Script caching** — codegen scripts are cached by sheet structure signature for reuse
  - `ScriptCache` with file-based storage (`~/.xlstruct/cache/`)
  - `compute_structure_signature()` hashes header values + column count + schema fields
  - Enabled by default (`cache_enabled=True`); configurable via `cache_dir`
  - Cache management API: `get()`, `put()`, `remove()`, `clear()`, `list_entries()`

## [0.2.0] - 2026-03-15

### Changed

- `suggest_schema()` now returns a dynamic Pydantic model class instead of source code string
- Use structured LLM output (`SuggestedFields`) for schema suggestion

### Added

- `schemas/suggest.py` — `FieldDef` and `SuggestedFields` response models

## [0.1.0] - 2026-03-10

### Added

- Schema-driven Excel extraction via Pydantic models
- Two extraction modes: direct LLM extraction and code generation
- `HybridReader` — calamine (Rust) + openpyxl dual-pass reader
- `CompressedEncoder` — token-aware sheet encoding with sampling
- `ChunkSplitter` — automatic chunking for large sheets
- Code generation pipeline with self-correction (Analyzer → Parser → Transformer)
- Sandboxed script execution (`SubprocessBackend`) with blocked imports and stripped credentials
- Multi-provider LLM support via Instructor (OpenAI, Anthropic, Gemini)
- Cloud storage support via fsspec (S3, Azure Blob, GCS)
- Async-first API with `*_sync()` convenience wrappers
- Typer CLI (`xlstruct extract`)
