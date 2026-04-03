"""Script validation via execution backend + output schema checking."""

import json
import logging
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from xlstruct.codegen.backends.base import ExecutionBackend
from xlstruct.codegen.backends.subprocess import SubprocessBackend
from xlstruct.codegen.executor import scan_blocked_imports

logger = logging.getLogger(__name__)


class ScriptValidationResult(BaseModel):
    """Result of running a generated script in a subprocess."""

    success: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    truncated_traceback: str = ""
    timed_out: bool = False


class ScriptValidator:
    """Validates generated scripts by executing them via an ExecutionBackend."""

    def __init__(
        self,
        timeout: int = 60,
        backend: ExecutionBackend | None = None,
    ) -> None:
        self._timeout = timeout
        self._backend = backend or SubprocessBackend()

    async def validate(
        self,
        code: str,
        source_path: str,
        output_schema: type[BaseModel] | None = None,
        total_data_rows: int | None = None,
    ) -> ScriptValidationResult:
        """Run generated script against the source file, capture result.

        Security: scans for blocked imports before execution.

        Args:
            code: The Python script code to validate.
            source_path: Path to the Excel file to pass as CLI argument.
            output_schema: Optional Pydantic model to validate stdout JSON against.
            total_data_rows: Approximate number of data rows in the source.
                Used for coverage diagnostics in output validation.

        Returns:
            ScriptValidationResult with success flag, output, and error info.
        """
        # * Pre-execution security scan
        blocked = scan_blocked_imports(code)
        if blocked:
            return ScriptValidationResult(
                success=False,
                exit_code=-1,
                truncated_traceback=(
                    f"SECURITY VIOLATIONS DETECTED: {', '.join(blocked)}. "
                    "Generated scripts may only import from the allowed module list: "
                    "openpyxl, python-calamine, pydantic, json, sys, re, datetime, "
                    "decimal, math, typing, enum, collections, dataclasses, copy, "
                    "itertools, functools, csv, and similar standard data "
                    "processing libraries. Dangerous builtins (exec, eval, open, "
                    "getattr, globals, etc.) and dunder escape patterns are also blocked."
                ),
            )

        # * Execute via backend
        exit_code, stdout, stderr = await self._backend.execute(code, source_path, self._timeout)

        # * Handle timeout
        if exit_code == -1:
            return ScriptValidationResult(
                success=False,
                exit_code=-1,
                timed_out=True,
                truncated_traceback=f"Script killed after {self._timeout}s timeout.",
            )

        if exit_code == 0:
            # * Filter records with null required fields
            if output_schema is not None:
                stdout = self._filter_by_required_fields(stdout, output_schema)

            # * Validate stdout JSON against schema
            if output_schema is not None:
                schema_error = self._validate_output(
                    stdout,
                    output_schema,
                    total_data_rows=total_data_rows,
                )
                if schema_error:
                    return ScriptValidationResult(
                        success=False,
                        exit_code=exit_code,
                        stdout=stdout,
                        stderr=stderr,
                        truncated_traceback=schema_error,
                    )
            return ScriptValidationResult(
                success=True,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
            )

        return ScriptValidationResult(
            success=False,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            truncated_traceback=self._extract_traceback(stderr),
        )

    @staticmethod
    def _filter_by_required_fields(
        stdout: str,
        schema: type[BaseModel],
    ) -> str:
        """Filter out records where Pydantic-required fields are null.

        Uses schema.model_fields to find fields without defaults,
        then removes records where any of those fields is None.
        This is a deterministic post-processing step — no LLM involvement.
        """
        try:
            raw: Any = json.loads(stdout.strip())
        except (json.JSONDecodeError, TypeError):
            return stdout

        if not isinstance(raw, list) or not raw:
            return stdout
        data = cast(list[dict[str, Any]], raw)

        required = [name for name, field in schema.model_fields.items() if field.is_required()]
        if not required:
            return stdout

        original_count = len(data)
        filtered: list[dict[str, Any]] = [
            record for record in data if all(record.get(f) is not None for f in required)
        ]

        if len(filtered) < original_count:
            logger.info(
                "Post-filter: %d → %d records (removed %d with null required fields)",
                original_count,
                len(filtered),
                original_count - len(filtered),
            )

        return json.dumps(filtered, ensure_ascii=False, indent=2, default=str)

    @staticmethod
    def _validate_output(
        stdout: str,
        schema: type[BaseModel],
        max_sample: int = 5,
        total_data_rows: int | None = None,
    ) -> str:
        """Validate stdout JSON against the Pydantic schema.

        Parses stdout as JSON array and validates a sample of items.
        Returns empty string if valid, error description if invalid.
        """
        stdout_stripped = stdout.strip()
        if not stdout_stripped:
            msg = (
                "OUTPUT VALIDATION ERROR: Script produced no output (empty stdout). "
                "The script must print JSON to stdout."
            )
            if total_data_rows:
                msg += (
                    f"\nThe source has approximately {total_data_rows} data rows — "
                    "the script should extract most of them."
                )
            return msg

        try:
            raw: Any = json.loads(stdout_stripped)
        except json.JSONDecodeError as e:
            # ^ Truncate stdout preview to avoid prompt bloat
            preview = stdout_stripped[:500]
            return (
                f"OUTPUT VALIDATION ERROR: stdout is not valid JSON.\n"
                f"JSONDecodeError: {e}\n"
                f"stdout preview:\n{preview}"
            )

        if not isinstance(raw, list):
            return (
                f"OUTPUT VALIDATION ERROR: Expected JSON array (list), "
                f"got {type(raw).__name__}. The script must output a JSON array of records."
            )
        data = cast(list[dict[str, Any]], raw)

        if len(data) == 0:
            msg = (
                "OUTPUT VALIDATION ERROR: Script produced an empty JSON array (0 records). "
                "The Excel file likely has data rows — check row iteration logic."
            )
            if total_data_rows:
                msg += (
                    f"\nThe source has approximately {total_data_rows} data rows. "
                    "Common causes of 0 output:\n"
                    "- if/continue conditions that skip ALL rows (too restrictive filtering)\n"
                    "- Wrong column used for group/category detection\n"
                    "- Iterating over wrong row range or wrong sheet"
                )
            return msg

        # * Coverage diagnostic: warn if extracted count is suspiciously low
        if total_data_rows and len(data) < total_data_rows * 0.1:
            coverage_pct = len(data) / total_data_rows * 100
            msg = (
                f"OUTPUT VALIDATION ERROR: Low coverage — extracted only {len(data)} "
                f"records from approximately {total_data_rows} data rows "
                f"({coverage_pct:.1f}% coverage).\n"
                "This likely means the row filtering/grouping logic is too restrictive.\n"
                "Check if/continue conditions — they may only match a small subset of "
                "valid data rows. Review your classification logic for ALL data patterns, "
                "not just the first few rows."
            )
            return msg

        # * Validate a sample of items against the schema
        errors: list[str] = []
        sample_indices = list(range(min(max_sample, len(data))))
        for idx in sample_indices:
            item = data[idx]
            try:
                schema.model_validate(item)
            except ValidationError as e:
                errors.append(f"Record {idx}: {e}")

        if errors:
            error_detail = "\n".join(errors)
            return (
                f"OUTPUT VALIDATION ERROR: {len(errors)}/{len(sample_indices)} sampled records "
                f"failed schema validation ({schema.__name__}).\n{error_detail}"
            )

        return ""

    @staticmethod
    def _extract_traceback(stderr: str, max_lines: int = 50, max_chars: int = 4000) -> str:
        """Extract and truncate the meaningful traceback from stderr.

        Keeps the last N lines (Python tracebacks end with the error message)
        and truncates to max_chars to prevent prompt bloat.
        """
        lines = stderr.strip().splitlines()

        if len(lines) > max_lines:
            lines = lines[-max_lines:]
            result = "[... truncated ...]\n" + "\n".join(lines)
        else:
            result = "\n".join(lines)

        if len(result) > max_chars:
            result = "[... truncated ...]\n" + result[-max_chars:]

        return result
