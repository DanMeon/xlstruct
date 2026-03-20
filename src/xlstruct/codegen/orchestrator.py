"""CodegenOrchestrator: Manages the multi-phase code generation pipeline.

Phases:
- Header Detection: Auto-detect header rows via lightweight LLM call.
- Phase 0 (Structure Analyzer): Analyze spreadsheet → MappingPlan.
- Phase 1 (Parser Agent): Generate parsing script with self-correction.
"""

import ast
import json
import logging
from typing import Any

from pydantic import BaseModel, ValidationError

from xlstruct.codegen.backends.base import ExecutionBackend
from xlstruct.codegen.backends.subprocess import SubprocessBackend
from xlstruct.codegen.engine import CodegenEngine
from xlstruct.codegen.schema_utils import get_schema_source
from xlstruct.codegen.validation import ScriptValidator
from xlstruct.config import SAMPLE_ROWS, ExtractionConfig, ExtractorConfig
from xlstruct.encoder._formatting import encode_raw_rows
from xlstruct.encoder.compressed import CompressedEncoder
from xlstruct.exceptions import CodegenValidationError, ExtractionError
from xlstruct.prompts.codegen import (
    ANALYZER_SYSTEM_PROMPT,
    CODEGEN_SYSTEM_PROMPT,
    HEADER_DETECTION_SYSTEM_PROMPT,
    build_analyzer_prompt,
    build_codegen_prompt,
    build_error_feedback,
    build_header_detection_prompt,
)
from xlstruct.schemas.codegen import CodegenAttempt, GeneratedScript
from xlstruct.schemas.core import SheetData
from xlstruct.schemas.usage import UsageTracker

logger = logging.getLogger(__name__)


class CodegenOrchestrator:
    """Orchestrates the multi-phase code generation pipeline."""

    def __init__(
        self,
        config: ExtractorConfig,
        backend: ExecutionBackend | None = None,
        tracker: UsageTracker | None = None,
    ) -> None:
        self._config = config
        self._engine = CodegenEngine(config, tracker=tracker)
        self._backend: ExecutionBackend = backend or SubprocessBackend()

    # * Public API

    async def detect_header_rows(self, sheet: SheetData) -> list[int]:
        """Auto-detect header rows via LLM when not explicitly provided.

        Uses a lightweight LLM call (~2K tokens) on the first 30 rows.
        """
        raw_encoded = encode_raw_rows(sheet, max_rows=30)
        prompt = build_header_detection_prompt(raw_encoded)
        detection = await self._engine.detect_headers(
            prompt, system_prompt=HEADER_DETECTION_SYSTEM_PROMPT
        )

        if not detection.header_rows:
            raise ExtractionError(
                "Header detection returned empty result. "
                "Please provide --header-rows explicitly."
            )

        logger.info(
            "Auto-detected header rows: %s (reason: %s)",
            detection.header_rows,
            detection.reasoning,
        )
        return detection.header_rows

    async def generate_script(
        self,
        source: str,
        full_sheet: SheetData,
        header_rows: list[int],
        extraction_config: ExtractionConfig,
    ) -> GeneratedScript:
        """Generate a standalone parsing script via LLM with self-correction.

        Args:
            source: File path (needed for subprocess validation).
            full_sheet: Full sheet data from reader.
            header_rows: 1-indexed header row numbers.
            extraction_config: Config with output_schema, instructions, etc.

        Returns:
            GeneratedScript with code and explanation.
        """
        # * Calculate total data rows for coverage diagnostics
        max_header_row = max(header_rows)
        total_data_rows = full_sheet.row_count - max_header_row

        # * Encode with sampling + structural metadata
        encoder = CompressedEncoder(sample_size=SAMPLE_ROWS)
        encoded = encoder.encode(full_sheet, header_rows=header_rows)

        # * Extract schema source code
        schema_source = get_schema_source(extraction_config.output_schema)

        file_name = source.rsplit("/", 1)[-1]

        # * Phase 0: Structure Analyzer — build column mapping plan
        logger.info("Phase 0 (Structure Analyzer): analyzing spreadsheet structure")
        analyzer_prompt = build_analyzer_prompt(
            encoded,
            schema_source,
            extraction_config.instructions,
            file_name=file_name,
            header_rows=header_rows,
        )
        mapping_plan = await self._engine.analyze(
            analyzer_prompt, system_prompt=ANALYZER_SYSTEM_PROMPT
        )
        logger.info(
            "Phase 0 complete: %d field mappings, row ratio=%s",
            len(mapping_plan.column_mappings),
            mapping_plan.row_to_records,
        )

        # * Build Phase 1 prompt with mapping plan
        phase1_prompt = build_codegen_prompt(
            encoded,
            schema_source,
            extraction_config.instructions,
            file_name=file_name,
            header_rows=header_rows,
            mapping_plan=mapping_plan,
            track_provenance=extraction_config.track_provenance,
        )

        # * Phase 1: Parser Agent — generate parsing script
        logger.info("Phase 1 (Parser Agent): generating parsing script")
        result, messages = await self._engine.generate(
            phase1_prompt, system_prompt=CODEGEN_SYSTEM_PROMPT
        )

        # * Verify syntax
        try:
            ast.parse(result.code)
        except SyntaxError as e:
            if self._config.max_codegen_retries <= 0:
                raise ExtractionError(
                    f"Generated script has syntax error at line {e.lineno}: {e.msg}"
                ) from e
            # ^ Syntax error treated as first failure in correction loop
            result = GeneratedScript(
                code=result.code,
                explanation=f"Initial script had syntax error: {e.msg}",
            )

        # * Validate + self-correct
        if self._config.max_codegen_retries > 0:
            result, _ = await self._validate_and_correct(
                result, messages, source,
                output_schema=extraction_config.output_schema,
                total_data_rows=total_data_rows,
            )

        return result

    async def run_extraction(
        self,
        source: str,
        script: GeneratedScript,
        output_schema: type[BaseModel],
    ) -> list[Any]:
        """Execute a validated script and parse JSON output into Pydantic models."""
        validator = ScriptValidator(timeout=self._config.codegen_timeout, backend=self._backend)
        validation = await validator.validate(
            script.code, source, output_schema=output_schema,
        )

        if not validation.success:
            raise ExtractionError(
                f"Codegen script execution failed: {validation.truncated_traceback}"
            )

        return self._parse_script_output(validation.stdout, output_schema)

    # * Private methods

    async def _validate_and_correct(
        self,
        result: GeneratedScript,
        messages: list[dict[str, Any]],
        source: str,
        output_schema: type[BaseModel] | None = None,
        total_data_rows: int | None = None,
    ) -> tuple[GeneratedScript, str]:
        """Run generated script in subprocess, correct on failure.

        Uses conversation-based correction: error feedback is appended to the
        existing messages history instead of re-sending the entire original prompt.
        """
        validator = ScriptValidator(timeout=self._config.codegen_timeout, backend=self._backend)
        attempts: list[CodegenAttempt] = []
        current_code = result.code
        current_result = result
        max_retries = self._config.max_codegen_retries

        for attempt_num in range(1, max_retries + 1):
            # * Syntax check before execution
            try:
                ast.parse(current_code)
            except SyntaxError as e:
                error_msg = f"SyntaxError at line {e.lineno}: {e.msg}"
                logger.info(
                    "Codegen attempt %d/%d: syntax error — %s",
                    attempt_num, max_retries, error_msg,
                )
                attempts.append(CodegenAttempt(
                    attempt=attempt_num,
                    code=current_code,
                    error=error_msg,
                ))
                if attempt_num == max_retries:
                    break
                current_result = await self._request_correction(
                    messages, error_msg,
                    attempt_num, max_retries,
                )
                current_code = current_result.code
                continue

            # * Validate by execution + output schema
            validation = await validator.validate(
                current_code, source,
                output_schema=output_schema,
                total_data_rows=total_data_rows,
            )

            if validation.success:
                if attempt_num > 1:
                    logger.info(
                        "Codegen attempt %d/%d: success after correction",
                        attempt_num, max_retries,
                    )
                return current_result, validation.stdout

            # * Record failed attempt
            logger.info(
                "Codegen attempt %d/%d: runtime error\n%s",
                attempt_num, max_retries, validation.truncated_traceback,
            )
            attempts.append(CodegenAttempt(
                attempt=attempt_num,
                code=current_code,
                error=validation.truncated_traceback,
            ))

            if attempt_num == max_retries:
                break

            # * Request correction via conversation history
            current_result = await self._request_correction(
                messages, validation.truncated_traceback,
                attempt_num, max_retries,
                timed_out=validation.timed_out,
            )
            current_code = current_result.code

        raise CodegenValidationError(
            f"Script generation failed after {len(attempts)} attempt(s). "
            f"Last error: {attempts[-1].error}",
            attempts=attempts,
        )

    async def _request_correction(
        self,
        messages: list[dict[str, Any]],
        error: str,
        attempt_num: int,
        max_retries: int,
        *,
        timed_out: bool = False,
    ) -> GeneratedScript:
        """Request a corrected script via conversation history."""
        # ^ Escalate temperature for diversity on retries
        retry_temp = self._config.temperature + 0.1 * attempt_num

        feedback = build_error_feedback(
            error,
            attempt=attempt_num,
            max_attempts=max_retries,
            timed_out=timed_out,
            timeout=self._config.codegen_timeout,
        )

        return await self._engine.correct(
            messages,
            feedback,
            temperature=retry_temp,
        )

    @staticmethod
    def _parse_script_output(stdout: str, schema: type[BaseModel]) -> list[Any]:
        """Parse JSON stdout from a codegen script into validated Pydantic models.

        Strips _source_row from records (if present) and stores it as an attribute
        on each validated model instance for provenance tracking.
        """
        try:
            data = json.loads(stdout.strip())
        except json.JSONDecodeError as e:
            raise ExtractionError(f"Failed to parse script output as JSON: {e}") from e

        if not isinstance(data, list):
            raise ExtractionError(
                f"Expected JSON array from script, got {type(data).__name__}"
            )

        results = []
        for i, item in enumerate(data):
            # ^ Extract provenance before validation (not part of schema)
            source_row = item.pop("_source_row", None) if isinstance(item, dict) else None
            try:
                record = schema.model_validate(item)
                if source_row is not None:
                    record._source_rows = [source_row]  # type: ignore[attr-defined]
                results.append(record)
            except ValidationError as e:
                logger.warning("Record %d failed validation, skipping: %s", i, e)
        return results
