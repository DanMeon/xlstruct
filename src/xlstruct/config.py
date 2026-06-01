"""XLStruct configuration."""

from enum import StrEnum
from pathlib import Path as PathLibPath
from typing import Any

import instructor
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class ExtractionMode(StrEnum):
    """Extraction mode selection.

    AUTO: Heuristic routing — ≤ SAMPLE_ROWS data rows → direct, otherwise → codegen.
    DIRECT: Force direct LLM extraction (per-call cost).
    CODEGEN: Force code generation (reusable script).
    """

    AUTO = "auto"
    DIRECT = "direct"
    CODEGEN = "codegen"


# * Provider-specific default kwargs for instructor.from_provider()
PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "anthropic": {"max_tokens": 8192},
}


class ExtractorConfig(BaseModel):
    """Configuration for Extractor instance."""

    provider: str = "anthropic/claude-sonnet-4-6"
    api_key: SecretStr | None = None
    max_retries: int = Field(default=3, ge=0)
    token_budget: int = Field(default=100_000, gt=0)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=8192, gt=0)
    max_codegen_retries: int = Field(default=3, ge=0)
    codegen_timeout: int = Field(default=60, gt=0)
    thinking: bool = Field(
        default=False,
        description="Enable Anthropic extended thinking mode. "
        "Temperature is forced to 1 when enabled.",
    )
    export_dir: PathLibPath | None = Field(
        default=None,
        description="Directory to save generated codegen scripts. "
        "When set, scripts are automatically exported after successful generation.",
    )
    cache_enabled: bool = Field(
        default=True,
        description="Enable script caching for codegen mode. "
        "When enabled, generated scripts are cached by sheet structure signature "
        "and reused for files with the same layout.",
    )
    cache_dir: PathLibPath | None = Field(
        default=None,
        description="Directory for script cache. "
        "Defaults to ~/.xlstruct/cache/ when cache_enabled is True.",
    )
    strict_formulas: bool = Field(
        default=True,
        description="When True, raise ReaderError if formula cells have no cached value "
        "(common with Google Sheets exports). When False, log a warning and fall back "
        "to showing the formula string instead of the computed value.",
    )
    evaluate_formulas: bool = Field(
        default=False,
        description="Evaluate formula cells using the formulas library. "
        "Requires: pip install xlstruct[formulas]",
    )
    csv_encoding: str = Field(
        default="utf-8",
        description="Text encoding for CSV files (e.g. 'euc-kr', 'cp1252', 'shift-jis'). "
        "'utf-8' transparently strips a BOM if present. Ignored for Excel formats.",
    )
    min_chunk_rows: int = Field(
        default=10,
        gt=0,
        description="Minimum rows per chunk to avoid degenerate cases.",
    )
    chunking_row_threshold: int = Field(
        default=100,
        gt=0,
        description="Force chunking for sheets exceeding this row count.",
    )
    max_concurrent_chunks: int = Field(
        default=5,
        gt=0,
        description="Max chunk LLM calls run concurrently for a single chunked sheet. "
        "Bounds parallelism to respect provider rate limits. Multiplies with "
        "extract_workbook(concurrency=...) on multi-sheet runs "
        "(worst-case in-flight calls = concurrency × max_concurrent_chunks).",
    )
    provider_options: dict[str, Any] = Field(default_factory=dict)
    storage_options: dict[str, Any] = Field(default_factory=dict)


class ExtractionConfig(BaseModel):
    """Per-extraction configuration. User-facing.

    Controls how sheet data is interpreted before LLM extraction.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    mode: ExtractionMode = Field(
        default=ExtractionMode.AUTO,
        description="Extraction mode: 'auto' (heuristic routing), 'direct' (LLM per call), "
        "'codegen' (generate reusable script).",
    )
    header_rows: list[int] | None = Field(
        default=None,
        description="1-indexed row numbers that form the header. "
        "Supports multi-index: [1, 2] means rows 1 and 2 are combined headers. "
        "None = auto-detect via LLM.",
    )
    output_schema: type[BaseModel] = Field(
        ...,
        description="Pydantic model class defining the target structure.",
    )
    sheet: str | None = Field(
        default=None,
        description="Target sheet name. None = first sheet.",
    )
    instructions: str | None = Field(
        default=None,
        description="Optional natural-language hints for the LLM. "
        "Include data transformation rules here if needed "
        "(e.g. 'Parse dates as YYYY-MM-DD', 'region_code: N → North').",
    )
    track_provenance: bool = Field(
        default=False,
        description="When True, each extracted record includes source row number(s) "
        "from the original Excel file. Stored in ExtractionResult.source_rows.",
    )
    include_confidence: bool = Field(
        default=False,
        description="When True, the LLM self-assesses confidence for each field. "
        "Scores are stored in ExtractionReport.field_confidences as numeric values "
        "(1.0=very_high, 0.75=high, 0.5=moderate, 0.25=low, 0.0=very_low).",
    )

    @field_validator("header_rows")
    @classmethod
    def _validate_header_rows(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return None
        if not v:
            raise ValueError("header_rows must contain at least one row number")
        if any(r < 1 for r in v):
            raise ValueError("header_rows must be 1-indexed (>= 1)")
        return sorted(v)


# ^ Internal constant — number of data rows sampled for LLM extraction
SAMPLE_ROWS = 20


def is_anthropic(provider: str) -> bool:
    """Check if provider is Anthropic (prompt caching supported)."""
    return provider.split("/")[0] == "anthropic"


def apply_cache_control(messages: list[dict[str, Any]], provider: str) -> list[dict[str, Any]]:
    """Apply the Anthropic prompt-caching marker to the static system prompt only.

    The system prompt is stable across calls, so caching it reliably hits. The user
    message holds variable sheet data — it changes every call, would essentially never
    hit, and tagging it only burns a cache breakpoint. So it is left unmarked.
    Returns messages unchanged for non-Anthropic providers.
    """
    if not is_anthropic(provider):
        return messages

    result: list[dict[str, Any]] = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        # ^ Cache only the stable system prompt, not the variable user message
        if role == "system" and isinstance(content, str):
            result.append(
                {
                    "role": role,
                    "content": [
                        {
                            "type": "text",
                            "text": content,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                }
            )
        else:
            result.append(msg)
    return result


def get_provider_kwargs(config: ExtractorConfig) -> dict[str, Any]:
    """Build provider-specific kwargs for instructor.from_provider().

    Handles per-provider differences (e.g. Anthropic requires max_tokens)
    in a single centralized function.
    """
    if "/" not in config.provider:
        raise ValueError(
            f"Invalid provider format: '{config.provider}'. "
            "Expected 'vendor/model' (e.g. 'openai/gpt-4o', 'anthropic/claude-sonnet-4-6')."
        )
    prefix = config.provider.split("/")[0]
    defaults = PROVIDER_DEFAULTS.get(prefix, {}).copy()

    # ^ Anthropic requires max_tokens at client level
    if prefix == "anthropic":
        defaults.setdefault("max_tokens", config.max_tokens)

    defaults.update(config.provider_options)
    return defaults


def _use_anthropic_thinking(config: "ExtractorConfig") -> bool:
    """Whether extended thinking applies — Anthropic-only, requires 'anthropic/<model>'."""
    return config.thinking and config.provider.startswith("anthropic/")


def build_instructor_client(config: "ExtractorConfig") -> Any:
    """Create async Instructor client honoring extended thinking.

    For Anthropic + thinking, uses ANTHROPIC_REASONING_TOOLS mode (the model is supplied
    per-call via thinking_call_kwargs). Otherwise uses the standard provider client.
    """
    if _use_anthropic_thinking(config):
        from anthropic import AsyncAnthropic  # type: ignore

        client_kwargs: dict[str, Any] = {}
        if config.api_key:
            client_kwargs["api_key"] = config.api_key.get_secret_value()
        return instructor.from_anthropic(  # type: ignore
            AsyncAnthropic(**client_kwargs),  # type: ignore
            mode=instructor.Mode.ANTHROPIC_REASONING_TOOLS,
        )

    kwargs = get_provider_kwargs(config)
    if config.api_key:
        kwargs["api_key"] = config.api_key.get_secret_value()
    return instructor.from_provider(
        config.provider,
        async_client=True,
        **kwargs,
    )


def thinking_call_kwargs(config: "ExtractorConfig") -> dict[str, Any]:
    """Per-call create_with_completion() kwargs for extended thinking.

    Empty when thinking is off. For Anthropic + thinking, forces temperature=1
    (Anthropic requirement), enables the thinking block, and carries max_tokens + model
    (from_anthropic does not embed the model the way from_provider does). Callers merge
    this last so it overrides any caller-supplied temperature.
    """
    if not _use_anthropic_thinking(config):
        return {}
    return {
        "temperature": 1,
        "thinking": {"type": "enabled", "budget_tokens": 10_000},
        "max_tokens": 16_000,
        "model": config.provider.split("/", 1)[1],
    }
