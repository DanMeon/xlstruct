"""ExtractionEngine: Instructor-based async LLM extraction."""

from typing import Any, TypeVar

import instructor
from pydantic import BaseModel

from xlstruct.config import ExtractorConfig, apply_cache_control, get_provider_kwargs
from xlstruct.exceptions import ExtractionError
from xlstruct.prompts.extraction import build_extraction_prompt
from xlstruct.prompts.system import SYSTEM_PROMPT
from xlstruct.schemas.usage import UsageTracker

T = TypeVar("T", bound=BaseModel)


class ExtractionEngine:
    """Wraps Instructor to extract structured data from encoded sheet text."""

    def __init__(self, config: ExtractorConfig, tracker: UsageTracker | None = None) -> None:
        self._config = config
        self._tracker = tracker
        self._client = self._build_client()

    def _build_client(self) -> Any:
        """Create async Instructor client with provider-specific kwargs."""
        kwargs = get_provider_kwargs(self._config)
        if self._config.api_key:
            kwargs["api_key"] = self._config.api_key.get_secret_value()
        return instructor.from_provider(
            self._config.provider,
            async_client=True,
            **kwargs,
        )

    async def extract(
        self,
        encoded_text: str,
        schema: type[T],
        instructions: str | None = None,
        *,
        is_sampled: bool = False,
        total_rows: int | None = None,
    ) -> list[T]:
        """Extract structured data matching schema from encoded sheet text.

        Always returns list[T] (single record = list of length 1).
        """
        prompt = build_extraction_prompt(
            encoded_text,
            instructions,
            is_sampled=is_sampled,
            total_rows=total_rows,
        )

        try:
            messages = apply_cache_control(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                self._config.provider,
            )
            result, completion = await self._client.create_with_completion(
                response_model=list[schema],  # type: ignore[valid-type]
                messages=messages,
                max_retries=self._config.max_retries,
                temperature=self._config.temperature,
            )
            if self._tracker:
                self._tracker.record("extraction", completion)
            return list(result)
        except Exception as e:
            raise ExtractionError(f"LLM extraction failed: {e}") from e
