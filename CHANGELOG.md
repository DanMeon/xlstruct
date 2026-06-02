# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.0] - 2026-06-02

### Security

- **OS-level sandbox is now the default for codegen execution (S1)** — untrusted, LLM-generated scripts run in `DockerBackend` when `xlstruct[docker]` is installed. The pre-execution AST scan is a best-effort filter, not a boundary (it has known bypasses), so it is no longer relied upon as the sole defense.
- **Hardened `SubprocessBackend` and demoted it to trusted/dev-only (S1)** — runs the script via `python -I` with a reduced builtins namespace (no `eval`/`exec`/`compile`/`breakpoint`/`input`), adds `RLIMIT_CPU`/`RLIMIT_NPROC`/`RLIMIT_NOFILE`/`RLIMIT_FSIZE`, kills the whole process group on timeout with a bounded drain, and fails closed when limits cannot be applied. It is explicitly NOT a security boundary.
- **Hardened `DockerBackend` (S2)** — the execution container now runs as a non-root user (`65534:65534`), drops all Linux capabilities (`CapDrop=["ALL"]`), mounts a read-only root filesystem with a writable `/tmp` tmpfs, and keeps `NetworkDisabled` + `no-new-privileges` + PID/memory/CPU limits. Optional `runtime` (e.g. gVisor `runsc`) and custom `seccomp_profile` knobs added to `DockerConfig`. The one-time package-install step stays permissive.
- **Integrity-protected codegen script cache (S3)** — the cache directory is `0700`, entries are `0600`, and every cached script carries an HMAC keyed by a per-user secret. Entries that fail verification are refused (never executed) and regenerated. The structure signature was widened from 64-bit to the full 256-bit SHA-256 digest.

### Added

- **`ExtractorConfig.codegen_sandbox`** (`"auto"` | `"docker"` | `"subprocess"`, default `"auto"`) — selects the codegen execution sandbox. An explicit `execution_backend` always overrides it.
- **`CodegenSecurityError`** + `ErrorCode.CODEGEN_NO_SANDBOX` — raised when codegen would run untrusted code with no sandbox available.
- **`SubprocessBackend(trusted=...)`** — acknowledge trusted/dev-only use and silence the "not a security boundary" warning.
- **`ExtractorConfig.csv_encoding`** — text encoding for CSV files (e.g. `euc-kr`, `cp1252`, `shift-jis`); `utf-8` strips a BOM if present. Ignored for Excel formats.
- **`ExtractorConfig.max_concurrent_chunks`** (default `5`) — bounds how many chunk LLM calls run concurrently for a single chunked sheet, to respect provider rate limits.
- **`xlstruct.__version__`** — the package version is now exposed in code, mirroring `pyproject.toml`.

### Changed

- **Extended thinking now applies to direct extraction and `suggest_schema`** (C1) — previously honored only on the codegen path. LLM client construction is unified so `thinking=True` reaches every call.
- **Chunked single-sheet extraction runs concurrently** (P1) — independent chunks are dispatched together under a bounded semaphore instead of serially, cutting wall-clock latency on large sheets.
- **Anthropic prompt caching marks only the static system prompt** (P2) — the variable per-call sheet data is no longer tagged, so a cache breakpoint is not wasted on content that never repeats.
- **Chunk sizing uses measured per-row token cost** (P4) — `ChunkSplitter` greedy bin-packs rows by real cost (reserving the header) so every chunk fits `token_budget` regardless of where heavy rows sit; the previous sampled, header-blind estimate could over-budget chunks.
- **Internal: `Extractor` decomposed into a thin facade over `ExtractionPipeline`** (A1, A2, A3) — orchestration, report assembly, and concurrency moved into `extraction/`; reader dispatch centralized into `READER_REGISTRY` shared by the extractor and MCP server; the unused `ExcelReader` protocol replaced by `WorkbookReader` + `ReaderOptions`. Public API and behavior unchanged.

### Changed (breaking)

- **Codegen with the default config now requires a sandbox.** When `xlstruct[docker]` is not installed, codegen fails closed with `CodegenSecurityError` instead of silently executing in a non-isolating subprocess. To restore the previous (unsandboxed) behavior in a trusted environment, set `ExtractorConfig(codegen_sandbox="subprocess")` or pass `execution_backend=SubprocessBackend(trusted=True)`. Direct (non-codegen) extraction is unaffected.
- **Existing codegen script caches are invalidated.** The widened structure signature changes cache keys, so cached scripts regenerate once on first use.
- `ScriptValidator` now requires an explicit `backend` argument (no implicit subprocess default).

### Fixed

- **Codegen no longer silently drops invalid records** (C2) — records that fail schema validation now raise instead of being skipped, so a partially mis-extracted result is surfaced rather than returned truncated.
- **Non-UTF-8 CSV files raise `ReaderError`** (C3) — a decode failure is wrapped with guidance and honors the configurable `csv_encoding`, instead of an unhandled `UnicodeDecodeError`.
- **Extraction error handling no longer mislabels internal bugs** (C4) — only the provider call is wrapped, so a post-processing bug is not reported as an LLM failure.
- **Empty (0-row) sheets fail fast** (B3) — both the configured and legacy `extract()` paths raise before spending an LLM call.
- **Confidence scoring no longer silently defaults** (B2) — a missing confidence level surfaces as an error instead of a fabricated `moderate` (0.5).
- **`find_empty_rows` no longer allocates a full row-range set** (B1) — wide/tall sheets skip the `set(range(...))` allocation; row-skipping behavior is unchanged.
- **Token counting tolerates special-token literals** — cell content like `<|endoftext|>` no longer raises during token estimation (now uses `encode_ordinary`).

## [0.6.0] - 2026-04-03

### Added

- **Configurable chunking thresholds** — `min_chunk_rows` and `chunking_row_threshold` fields in `ExtractorConfig`
- **Cross-sheet token budget validation** — `extract_cross_sheet()` raises `ExtractionError` when combined encoding exceeds `token_budget`
- **MCP schema builder: complex types** — `build_model_from_schema_json()` now supports `list`, nested `object`, and `enum` types
- **Codegen MappingPlan validation** — empty mappings warn, duplicate `schema_field` entries are auto-deduplicated between Phase 0 and Phase 1
- **CSV ISO date inference** — `CsvReader` detects ISO date/datetime strings and sets `data_type="d"`
- **Sandbox getattr detection** — `scan_blocked_imports()` detects `getattr()`/`setattr()`/`delattr()` calls with blocked dunder string arguments

### Fixed

- **CSV BOM handling** — UTF-8 BOM (`\ufeff`) is now stripped automatically via `utf-8-sig` encoding
- **Record filter data loss** — `_filter_by_required_fields()` no longer drops records with empty-string (`""`) required fields

### Changed

- `ExtractorConfig` numeric fields now have Pydantic `Field` constraints (`token_budget: gt=0`, `temperature: ge=0, le=2.0`, etc.)
- `count_tokens()` utility added to `_tokens.py` for plain text token counting

## [0.5.0] - 2026-03-25

### Added

- **CSV dialect auto-detection** — semicolon, tab, and pipe delimiters
- **Cell number format** — `CellData.number_format` field stores Excel Number Format String from openpyxl
- **Strict formulas toggle** — `strict_formulas` config for graceful handling of uncached formula cells
- **Formula evaluation** — optional evaluation via `formulas` library (`evaluate_formulas` config); `xlstruct[formulas]` extra
- **Cell-address provenance** — `source_cells` in `ExtractionReport` for cell-address level tracking
- **Per-field confidence scores** — LLM self-assessment via `include_confidence` config
- **Schema suggestion Python API** — `suggest_schema_source()` / `suggest_schema_source_sync()` public functions
- **`render_schema_source()` utility** in `xlstruct.suggest` module
- **Streaming extraction** — `Extractor.stream()` / `stream_sync()` AsyncGenerator-based streaming
- **Cross-sheet extraction** — `Extractor.extract_cross_sheet()` extracts from multiple sheets into a unified Pydantic model
- **CLI `extract` command** with `--schema`, `--mode`, `--format` options
- **CLI `batch` command** for multi-file extraction
- **CLI `cache` subcommands** — `cache list`, `cache clear`, `cache remove`
- **MCP server** (`xlstruct-mcp`) with 7 tools for AI agent integration; `xlstruct[mcp]` extra

### Changed

- `summarize_column_types()` now uses `number_format` for more accurate currency/percentage/date detection
- CLI entry point changed to `_cli_entry` (adds cwd to `sys.path` for local schema imports)
- `CompressedEncoder` instantiation now uses `create_encoder()` factory

### Fixed

- Confidence schema wrapping now correctly excludes provenance fields (`source_rows`, `source_cells`)

## [0.4.1] - 2026-03-20

### Added

- **Python 3.13 support** — added to CI test matrix and classifiers

### Changed

- Enforce `ruff format` in pre-commit hooks and CI

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
