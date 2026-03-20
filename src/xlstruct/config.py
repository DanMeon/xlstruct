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
    max_retries: int = 3
    token_budget: int = 100_000
    temperature: float = 0.0
    max_tokens: int = 8192
    max_codegen_retries: int = 3
    codegen_timeout: int = 60
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


def apply_cache_control(
    messages: list[dict[str, Any]], provider: str
) -> list[dict[str, Any]]:
    """Apply Anthropic prompt caching markers to messages.

    Wraps system and first user message content with cache_control ephemeral markers.
    Returns messages unchanged for non-Anthropic providers.
    """
    if not is_anthropic(provider):
        return messages

    result: list[dict[str, Any]] = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        # ^ Apply cache_control to system prompt and first user message
        if role in ("system", "user") and isinstance(content, str):
            result.append({
                "role": role,
                "content": [
                    {
                        "type": "text",
                        "text": content,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            })
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


def build_instructor_client(config: "ExtractorConfig") -> Any:
    """Create async Instructor client with provider-specific kwargs.

    Centralizes the common pattern: get_provider_kwargs → inject api_key → from_provider.
    """
    kwargs = get_provider_kwargs(config)
    if config.api_key:
        kwargs["api_key"] = config.api_key.get_secret_value()
    return instructor.from_provider(
        config.provider,
        async_client=True,
        **kwargs,
    )
