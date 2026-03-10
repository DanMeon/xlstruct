"""CodegenEngine: LLM-based transformation script generator."""

from typing import Any

import instructor

from xlstruct.config import ExtractorConfig, apply_cache_control, get_provider_kwargs
from xlstruct.exceptions import ExtractionError
from xlstruct.prompts.codegen import CODEGEN_SYSTEM_PROMPT
from xlstruct.schemas.codegen import (
    GeneratedScript,
    HeaderDetectionResult,
    MappingPlan,
)
from xlstruct.schemas.usage import UsageTracker


class CodegenEngine:
    """Generates standalone Python transformation scripts via LLM."""

    def __init__(self, config: ExtractorConfig, tracker: UsageTracker | None = None) -> None:
        self._config = config
        self._tracker = tracker
        self._model: str | None = None
        self._client = self._build_client()

    def _build_client(self) -> Any:
        """Create async Instructor client with provider-specific kwargs."""
        kwargs = get_provider_kwargs(self._config)
        if self._config.api_key:
            kwargs["api_key"] = self._config.api_key.get_secret_value()

        # ^ Anthropic thinking requires ANTHROPIC_REASONING_TOOLS mode
        if self._config.thinking and self._config.provider.startswith("anthropic/"):
            from anthropic import AsyncAnthropic

            model = self._config.provider.split("/", 1)[1]
            client_kwargs: dict[str, Any] = {}
            if self._config.api_key:
                client_kwargs["api_key"] = self._config.api_key.get_secret_value()
            client = instructor.from_anthropic(
                AsyncAnthropic(**client_kwargs),
                mode=instructor.Mode.ANTHROPIC_REASONING_TOOLS,
            )
            # ^ Store model name for create() calls
            self._model = model
            return client

        self._model = None
        return instructor.from_provider(
            self._config.provider,
            async_client=True,
            **kwargs,
        )

    def _thinking_kwargs(self, temperature: float) -> dict[str, Any]:
        """Build kwargs for create() with optional extended thinking.

        When thinking is enabled, forces temperature=1
        (required by Anthropic API) and sets a default budget.
        """
        if self._config.thinking:
            result: dict[str, Any] = {
                "temperature": 1,
                "thinking": {"type": "enabled", "budget_tokens": 10_000},
                "max_tokens": 16_000,
            }
            if self._model:
                result["model"] = self._model
            return result
        return {"temperature": temperature}

    async def detect_headers(
        self,
        prompt: str,
        *,
        system_prompt: str,
    ) -> HeaderDetectionResult:
        """Detect header rows from raw spreadsheet data via LLM."""
        try:
            kwargs = self._thinking_kwargs(temperature=0.0)
            messages = apply_cache_control(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                self._config.provider,
            )
            result, completion = await self._client.create_with_completion(
                response_model=HeaderDetectionResult,
                messages=messages,
                max_retries=self._config.max_retries,
                **kwargs,
            )
            if self._tracker:
                self._tracker.record("header_detection", completion)
            return result  # type: ignore[no-any-return]
        except Exception as e:
            raise ExtractionError(f"Header detection failed: {e}") from e

    async def analyze(
        self,
        prompt: str,
        *,
        system_prompt: str,
    ) -> MappingPlan:
        """Analyze spreadsheet structure and produce a column mapping plan."""
        try:
            kwargs = self._thinking_kwargs(temperature=0.0)
            messages = apply_cache_control(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                self._config.provider,
            )
            result, completion = await self._client.create_with_completion(
                response_model=MappingPlan,
                messages=messages,
                max_retries=self._config.max_retries,
                **kwargs,
            )
            if self._tracker:
                self._tracker.record("analyzer", completion)
            return result  # type: ignore[no-any-return]
        except Exception as e:
            raise ExtractionError(f"Structure analysis failed: {e}") from e

    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: str = CODEGEN_SYSTEM_PROMPT,
    ) -> tuple[GeneratedScript, list[dict[str, Any]]]:
        """Generate a script from a pre-built prompt.

        Returns the generated script AND the conversation history (messages list).
        The history includes the assistant's response, enabling multi-turn
        correction without re-sending the original prompt.
        """
        messages: list[dict[str, Any]] = apply_cache_control(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            self._config.provider,
        )
        try:
            kwargs = self._thinking_kwargs(temperature=self._config.temperature)
            result, completion = await self._client.create_with_completion(
                response_model=GeneratedScript,
                messages=messages,
                max_retries=self._config.max_retries,
                **kwargs,
            )
            if self._tracker:
                self._tracker.record("codegen", completion)
            # ^ Track assistant response in conversation history
            messages.append({"role": "assistant", "content": result.code})
            return result, messages
        except Exception as e:
            raise ExtractionError(f"Code generation failed: {e}") from e

    async def correct(
        self,
        messages: list[dict[str, Any]],
        error_feedback: str,
        *,
        temperature: float = 0.0,
    ) -> GeneratedScript:
        """Generate a corrected script using conversation history.

        Appends lightweight error feedback to the existing messages list,
        avoiding re-sending the original prompt.
        """
        messages.append({"role": "user", "content": error_feedback})

        try:
            kwargs = self._thinking_kwargs(temperature=temperature)
            result, completion = await self._client.create_with_completion(
                response_model=GeneratedScript,
                messages=messages,
                max_retries=self._config.max_retries,
                **kwargs,
            )
            if self._tracker:
                self._tracker.record("codegen_correction", completion)
            # ^ Track corrected response in conversation history
            messages.append({"role": "assistant", "content": result.code})
            return result  # type: ignore[no-any-return]
        except Exception as e:
            raise ExtractionError(f"Code correction failed: {e}") from e
