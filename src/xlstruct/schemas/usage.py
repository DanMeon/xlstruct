"""Token usage tracking for LLM calls."""

import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def _extract_usage(completion: Any) -> tuple[int, int, int, int]:
    """Extract token counts from a raw LLM completion.

    Handles both OpenAI (prompt_tokens/completion_tokens)
    and Anthropic (input_tokens/output_tokens) naming conventions.
    Also extracts Anthropic prompt caching metrics.

    Returns:
        (input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens)
    """
    usage = getattr(completion, "usage", None)
    if usage is None:
        return 0, 0, 0, 0
    input_t = getattr(usage, "input_tokens", 0) or getattr(usage, "prompt_tokens", 0)
    output_t = getattr(usage, "output_tokens", 0) or getattr(usage, "completion_tokens", 0)
    # ^ Anthropic prompt caching fields
    cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0

    # ^ OpenAI automatic caching: usage.prompt_tokens_details.cached_tokens
    if cache_read == 0:
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cache_read = getattr(details, "cached_tokens", 0) or 0

    return input_t or 0, output_t or 0, cache_create, cache_read


class TokenUsage(BaseModel):
    """Token usage summary for an extraction operation."""

    llm_calls: int = Field(default=0, description="Number of LLM API calls made")
    input_tokens: int = Field(default=0, description="Total input/prompt tokens")
    output_tokens: int = Field(default=0, description="Total output/completion tokens")
    total_tokens: int = Field(default=0, description="Sum of input + output tokens")
    cache_creation_tokens: int = Field(
        default=0, description="Tokens written to prompt cache (Anthropic only)"
    )
    cache_read_tokens: int = Field(
        default=0, description="Tokens read from prompt cache (Anthropic + OpenAI)"
    )
    breakdown: list[tuple[str, int, int]] = Field(
        default_factory=list,
        description="Per-call breakdown: (label, input_tokens, output_tokens)",
    )

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            llm_calls=self.llm_calls + other.llm_calls,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cache_creation_tokens=self.cache_creation_tokens + other.cache_creation_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
        )


class UsageTracker:
    """Accumulates token usage across multiple LLM calls."""

    def __init__(self) -> None:
        self._calls: list[tuple[str, int, int]] = []
        self._cache_creation: int = 0
        self._cache_read: int = 0

    def record(self, label: str, completion: Any) -> None:
        """Extract usage from a raw completion and record it."""
        input_t, output_t, cache_create, cache_read = _extract_usage(completion)
        if input_t == 0 and output_t == 0:
            return
        self._calls.append((label, input_t, output_t))
        self._cache_creation += cache_create
        self._cache_read += cache_read

        cache_info = ""
        if cache_create or cache_read:
            cache_info = f", cache_create={cache_create:,}, cache_read={cache_read:,}"
        logger.info(
            "LLM [%s]: input=%s, output=%s tokens%s",
            label, f"{input_t:,}", f"{output_t:,}", cache_info,
        )

    def snapshot(self) -> TokenUsage:
        """Return a frozen snapshot of accumulated usage."""
        total_in = sum(t[1] for t in self._calls)
        total_out = sum(t[2] for t in self._calls)
        return TokenUsage(
            llm_calls=len(self._calls),
            input_tokens=total_in,
            output_tokens=total_out,
            total_tokens=total_in + total_out,
            cache_creation_tokens=self._cache_creation,
            cache_read_tokens=self._cache_read,
            breakdown=list(self._calls),
        )

    def reset(self) -> None:
        self._calls.clear()
        self._cache_creation = 0
        self._cache_read = 0
