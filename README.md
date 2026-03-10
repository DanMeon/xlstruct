# XLStruct

[![CI](https://github.com/DanMeon/xlstruct/actions/workflows/ci.yml/badge.svg)](https://github.com/DanMeon/xlstruct/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/xlstruct)](https://pypi.org/project/xlstruct/)
[![Python 3.11+](https://img.shields.io/pypi/pyversions/xlstruct)](https://pypi.org/project/xlstruct/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

LLM-powered Excel/CSV parser — Define a Pydantic schema, get structured data from any spreadsheet.

```
Excel File + Pydantic Schema  →  LLM  →  Validated Structured Data
```

## Features

- **Schema-driven extraction** — Define a Pydantic model, get validated instances from any spreadsheet. No parsing code needed.
- **Excel + CSV** — `.xlsx`, `.xlsm`, `.xltx`, `.xltm`, `.xls`, and `.csv` supported out of the box
- **Any Excel layout** — Flat tables, merged cells, multi-level headers, form+table hybrids — handled by a single API
- **Two extraction modes** — Direct LLM extraction for small sheets; code generation for large ones. Auto-routed by sheet size, or choose manually.
- **Reusable scripts** — Codegen mode produces a standalone Python script. Run it without LLM calls — pay for generation once, use forever.
- **Schema suggestion** — `suggest_schema()` analyzes a spreadsheet and generates a Pydantic model for you
- **Fast hybrid reader** — calamine (Rust) for speed + openpyxl for formula extraction. Both passes in one call.
- **Token usage tracking** — Every extraction returns token counts, per-call breakdown, and prompt cache hit metrics
- **Prompt caching** — Anthropic cache_control markers applied automatically; OpenAI cached_tokens tracked
- **Token-aware encoding** — Auto-selects encoding strategy (markdown vs compressed) and chunks large sheets to fit within token budget
- **Sandboxed execution** — Generated scripts run in a subprocess with blocked imports (network, subprocess) and stripped credentials
- **Multi-provider LLM** — OpenAI, Anthropic, Gemini via [Instructor](https://github.com/jxnl/instructor)
- **Cloud storage** — Read from S3, Azure Blob, GCS via fsspec
- **Async-first** — Async API with sync convenience wrappers. Jupyter-compatible via nest_asyncio.

## Installation

```bash
pip install xlstruct
```

### Extras

```bash
# LLM providers
pip install "xlstruct[openai]"
pip install "xlstruct[anthropic]"
pip install "xlstruct[gemini]"

# Cloud storage
pip install "xlstruct[s3]"        # AWS S3
pip install "xlstruct[azure]"     # Azure Blob Storage
pip install "xlstruct[gcs]"       # Google Cloud Storage

# Everything
pip install "xlstruct[all]"
```

## Quick Start

### 1. Define a Pydantic schema

```python
from pydantic import BaseModel, Field

class InvoiceItem(BaseModel):
    description: str = Field(description="Item description")
    quantity: int
    unit_price: float
    total: float
```

### 2. Extract data

```python
from xlstruct import Extractor

extractor = Extractor(provider="openai/gpt-4o")
results = extractor.extract_sync("invoice.xlsx", schema=InvoiceItem)

for item in results:
    print(item.model_dump())
```

### Async usage

```python
import asyncio
from xlstruct import Extractor

async def main():
    extractor = Extractor(provider="anthropic/claude-sonnet-4-6")
    results = await extractor.extract("invoice.xlsx", schema=InvoiceItem)
    for item in results:
        print(item.model_dump())

asyncio.run(main())
```

### Fine-grained control with ExtractionConfig

```python
from xlstruct import ExtractionConfig

config = ExtractionConfig(
    output_schema=InvoiceItem,
    sheet="Sheet1",
    header_rows=[1],
    instructions="Parse dates as YYYY-MM-DD. Skip empty rows.",
)

results = extractor.extract_sync("invoice.xlsx", extraction_config=config)
```

### Schema Suggestion

Don't know the spreadsheet structure? Let the LLM suggest a schema:

```python
code = extractor.suggest_schema_sync("unknown_data.xlsx")
print(code)  # Prints a Pydantic model definition
```

```python
# With hints
code = extractor.suggest_schema_sync(
    "report.xlsx",
    sheet="Q1 Sales",
    instructions="Focus on financial columns only",
)
```

### Token Usage Tracking

Every extraction returns token usage details:

```python
results = extractor.extract_sync("invoice.xlsx", schema=InvoiceItem)

print(results.usage.llm_calls)        # Number of LLM API calls
print(results.usage.input_tokens)     # Total input tokens
print(results.usage.output_tokens)    # Total output tokens
print(results.usage.total_tokens)     # Sum
print(results.usage.cache_read_tokens)  # Prompt cache hits (Anthropic + OpenAI)
print(results.usage.breakdown)        # Per-call breakdown: [(label, in, out), ...]
```

### CSV Support

CSV files work with the same API — no extra configuration needed:

```python
results = extractor.extract_sync("data.csv", schema=MyModel)
```

## Extraction Modes

XLStruct auto-routes based on data row count:

| Mode | Trigger | How it works |
|------|---------|-------------|
| **Direct** | ≤ 20 data rows | Sheet encoded as markdown → LLM → Pydantic models |
| **Codegen** | > 20 data rows | LLM generates a standalone Python parsing script → runs in sandbox |

You can force a specific mode:

```python
from xlstruct import ExtractionConfig, ExtractionMode

config = ExtractionConfig(
    mode=ExtractionMode.CODEGEN,    # Force code generation
    output_schema=MySchema,
    header_rows=[1],
)
```

### Code Generation Pipeline

1. **Header Detection** — Auto-detect header rows via lightweight LLM call
2. **Phase 0 (Analyzer)** — LLM analyzes spreadsheet structure → `MappingPlan`
3. **Phase 1 (Parser Agent)** — LLM generates openpyxl-based parsing script → validated via subprocess
4. **Phase 2 (Transformer)** — Optional: adds data transformation rules

Each phase includes self-correction — errors are fed back to the LLM (up to `max_codegen_retries`).

### Generate Standalone Scripts

```python
script = extractor.generate_script_sync("report.xlsx", config)
print(script.code)          # Reusable Python script
print(script.explanation)   # How it works
```

## Cloud Storage

```python
# AWS S3
results = await extractor.extract(
    "s3://my-bucket/data/report.xlsx",
    schema=MySchema,
    key="AWS_KEY", secret="AWS_SECRET",
)

# Azure Blob Storage
results = await extractor.extract(
    "az://my-container/report.xlsx",
    schema=MySchema,
    account_name="...", account_key="...",
)

# Google Cloud Storage
results = await extractor.extract(
    "gs://my-bucket/report.xlsx",
    schema=MySchema,
    token="/path/to/credentials.json",
)
```

## CLI

```bash
# Basic usage
xlstruct extract invoice.xlsx --schema myapp.models:InvoiceItem

# With options
xlstruct extract report.xlsx \
  --schema myapp.models:SalesRecord \
  --provider anthropic/claude-sonnet-4-6 \
  --sheet "Q1 Sales" \
  --instructions "Ignore summary rows" \
  --output results.json
```

## Configuration

### ExtractorConfig

| Parameter | Default | Description |
|-----------|---------|-------------|
| `provider` | `"anthropic/claude-sonnet-4-6"` | LLM provider (`openai/gpt-4o`, `anthropic/claude-sonnet-4-6`, etc.) |
| `api_key` | `None` | API key (falls back to environment variables) |
| `max_retries` | `3` | LLM API retry count |
| `token_budget` | `100_000` | Max tokens per sheet |
| `temperature` | `0.0` | LLM temperature |
| `max_codegen_retries` | `3` | Self-correction attempts for code generation |
| `codegen_timeout` | `60` | Script execution timeout in seconds |
| `export_dir` | `None` | Directory to auto-save generated codegen scripts |

```python
from xlstruct import ExtractorConfig

config = ExtractorConfig(
    provider="openai/gpt-4o",
    temperature=0.0,
    token_budget=200_000,
)
extractor = Extractor(config=config)
```

### Docker Backend

For OS-level sandboxing, pass a `DockerBackend` via `execution_backend`:

```bash
pip install xlstruct[docker]
```

```python
from xlstruct import Extractor
from xlstruct.codegen.backends.docker import DockerBackend, DockerConfig

extractor = Extractor(
    execution_backend=DockerBackend(
        config=DockerConfig(image="python:3.12-slim", mem_limit="1g"),
    ),
)
```

## Architecture

```
Storage (fsspec) → Reader (HybridReader / CsvReader) → CompressedEncoder → LLM Engine → Pydantic
```

### Reader

**Excel** — `HybridReader` uses a 2-pass approach:
- **Pass 1 (calamine, Rust)** — values, merged cells, data types, dimensions
- **Pass 2 (openpyxl, read-only)** — formula strings (`.xlsx`/`.xlsm` only)

**CSV** — `CsvReader` uses Python stdlib `csv` module. No extra dependencies.

### Encoder

`CompressedEncoder` converts sheet data to markdown tables with structural metadata:
- Column types, formula patterns, merged regions
- Optional head+tail sampling for large sheets (20 rows by default)

### Exceptions

```
XLStructError (base)
├── StorageError
├── ReaderError
├── ExtractionError
└── CodegenValidationError
```

## Development

```bash
uv sync                            # Install dependencies
uv run pytest tests/ -v            # Run all tests
uv run ruff check src/ tests/      # Lint
uv run mypy src/xlstruct/          # Type check
```

## License

MIT
