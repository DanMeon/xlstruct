"""CodegenEngine: LLM-based transformation script generator."""

from typing import Any, TypeVar

from pydantic import BaseModel

from xlstruct.config import (
    ExtractorConfig,
    apply_cache_control,
    build_instructor_client,
    thinking_call_kwargs,
)
from xlstruct.exceptions import ErrorCode, ExtractionError
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
        self._client = build_instructor_client(config)
        self._thinking_kwargs = thinking_call_kwargs(config)

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
            call_kwargs = {"temperature": temperature, **self._thinking_kwargs}
            result, completion = await self._client.create_with_completion(
                response_model=response_model,
                messages=messages,
                max_retries=self._config.max_retries,
                **call_kwargs,
            )
            if self._tracker:
                self._tracker.record(label, completion)
            return result  # type: ignore
        except Exception as e:
            raise ExtractionError(f"{error_msg}: {e}", code=ErrorCode.EXTRACTION_LLM_FAILED) from e

    def _build_messages(self, system_prompt: str, user_prompt: str) -> list[dict[str, Any]]:
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
        return await self._call_llm(MappingPlan, messages, "analyzer", "Structure analysis failed")

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
