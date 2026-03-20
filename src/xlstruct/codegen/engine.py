"""CodegenEngine: LLM-based transformation script generator."""

from typing import Any, TypeVar

import instructor
from pydantic import BaseModel

from xlstruct.config import ExtractorConfig, apply_cache_control, build_instructor_client
from xlstruct.exceptions import ExtractionError
from xlstruct.prompts.codegen import CODEGEN_SYSTEM_PROMPT
from xlstruct.schemas.codegen import (
    GeneratedScript,
    HeaderDetectionResult,
    MappingPlan,
)
from xlstruct.schemas.usage import UsageTracker

_T = TypeVar("_T", bound=BaseModel)


class CodegenEngine:
    """Generates standalone Python transformation scripts via LLM."""

    def __init__(self, config: ExtractorConfig, tracker: UsageTracker | None = None) -> None:
        self._config = config
        self._tracker = tracker
        self._model: str | None = None
        self._client = self._build_client()

    def _build_client(self) -> Any:
        """Create async Instructor client with provider-specific kwargs."""
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
        return build_instructor_client(self._config)

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

    async def _call_llm(
        self,
        response_model: type[_T],
        messages: list[dict[str, Any]],
        label: str,
        error_msg: str,
        *,
        temperature: float = 0.0,
    ) -> _T:
        """Execute LLM call with usage tracking and error handling."""
        try:
            kwargs = self._thinking_kwargs(temperature=temperature)
            result, completion = await self._client.create_with_completion(
                response_model=response_model,
                messages=messages,
                max_retries=self._config.max_retries,
                **kwargs,
            )
            if self._tracker:
                self._tracker.record(label, completion)
            return result  # type: ignore[no-any-return]
        except Exception as e:
            raise ExtractionError(f"{error_msg}: {e}") from e

    def _build_messages(
        self, system_prompt: str, user_prompt: str
    ) -> list[dict[str, Any]]:
        """Build and cache-control messages for an LLM call."""
        return apply_cache_control(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            self._config.provider,
        )

    async def detect_headers(
        self,
        prompt: str,
        *,
        system_prompt: str,
    ) -> HeaderDetectionResult:
        """Detect header rows from raw spreadsheet data via LLM."""
        messages = self._build_messages(system_prompt, prompt)
        return await self._call_llm(
            HeaderDetectionResult, messages, "header_detection", "Header detection failed"
        )

    async def analyze(
        self,
        prompt: str,
        *,
        system_prompt: str,
    ) -> MappingPlan:
        """Analyze spreadsheet structure and produce a column mapping plan."""
        messages = self._build_messages(system_prompt, prompt)
        return await self._call_llm(
            MappingPlan, messages, "analyzer", "Structure analysis failed"
        )

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
        messages = self._build_messages(system_prompt, prompt)
        result = await self._call_llm(
            GeneratedScript,
            messages,
            "codegen",
            "Code generation failed",
            temperature=self._config.temperature,
        )
        # ^ Track assistant response in conversation history
        messages.append({"role": "assistant", "content": result.code})
        return result, messages

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
        result = await self._call_llm(
            GeneratedScript,
            messages,
            "codegen_correction",
            "Code correction failed",
            temperature=temperature,
        )
        # ^ Track corrected response in conversation history
        messages.append({"role": "assistant", "content": result.code})
        return result
