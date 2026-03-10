"""XLStruct exception hierarchy."""

from typing import Any


class XLStructError(Exception):
    """Base exception for all XLStruct errors."""


class StorageError(XLStructError):
    """File access error (wraps FileNotFoundError, PermissionError, etc.)."""


class ReaderError(XLStructError):
    """Excel parsing error (wraps openpyxl errors)."""


class ExtractionError(XLStructError):
    """LLM extraction error (wraps Instructor/API errors)."""


class CodegenValidationError(XLStructError):
    """All codegen retry attempts exhausted."""

    def __init__(self, message: str, attempts: "list[Any]") -> None:  # list[CodegenAttempt]
        super().__init__(message)
        self.attempts = attempts
