# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- **OS-level sandbox is now the default for codegen execution (S1)** — untrusted, LLM-generated scripts run in `DockerBackend` when `xlstruct[docker]` is installed. The pre-execution AST scan is a best-effort filter, not a boundary (it has known bypasses), so it is no longer relied upon as the sole defense.
- **Hardened `SubprocessBackend` and demoted it to trusted/dev-only (S1)** — runs the script via `python -I` with a reduced builtins namespace (no `eval`/`exec`/`compile`/`breakpoint`/`input`), adds `RLIMIT_CPU`/`RLIMIT_NPROC`/`RLIMIT_NOFILE`/`RLIMIT_FSIZE`, kills the whole process group on timeout with a bounded drain, and fails closed when limits cannot be applied. It is explicitly NOT a security boundary.
- **Hardened `DockerBackend` (S2)** — the execution container now runs as a non-root user (`65534:65534`), drops all Linux capabilities (`CapDrop=["ALL"]`), mounts a read-only root filesystem with a writable `/tmp` tmpfs, and keeps `NetworkDisabled` + `no-new-privileges` + PID/memory/CPU limits. Optional `runtime` (e.g. gVisor `runsc`) and custom `seccomp_profile` knobs added to `DockerConfig`. The one-time package-install step stays permissive.
- **Integrity-protected codegen script cache (S3)** — the cache directory is `0700`, entries are `0600`, and every cached script carries an HMAC keyed by a per-user secret. Entries that fail verification are refused (never executed) and regenerated. The structure signature was widened from 64-bit to the full 256-bit SHA-256 digest.

### Added

- **`ExtractorConfig.codegen_sandbox`** (`"auto"` | `"docker"` | `"subprocess"`, default `"auto"`) — selects the codegen execution sandbox. An explicit `execution_backend` always overrides it.
- **`CodegenSecurityError`** + `ErrorCode.CODEGEN_NO_SANDBOX` — raised when codegen would run untrusted code with no sandbox available.
- **`SubprocessBackend(trusted=...)`** — acknowledge trusted/dev-only use and silence the "not a security boundary" warning.

### Changed (breaking)

- **Codegen with the default config now requires a sandbox.** When `xlstruct[docker]` is not installed, codegen fails closed with `CodegenSecurityError` instead of silently executing in a non-isolating subprocess. To restore the previous (unsandboxed) behavior in a trusted environment, set `ExtractorConfig(codegen_sandbox="subprocess")` or pass `execution_backend=SubprocessBackend(trusted=True)`. Direct (non-codegen) extraction is unaffected.
- **Existing codegen script caches are invalidated.** The widened structure signature changes cache keys, so cached scripts regenerate once on first use.
- `ScriptValidator` now requires an explicit `backend` argument (no implicit subprocess default).

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
