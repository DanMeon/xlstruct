"""XLStruct exception hierarchy."""

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """Machine-readable error codes for programmatic handling."""

    # * Storage errors
    STORAGE_NOT_FOUND = "STORAGE_NOT_FOUND"
    STORAGE_PERMISSION_DENIED = "STORAGE_PERMISSION_DENIED"
    STORAGE_READ_FAILED = "STORAGE_READ_FAILED"

    # * Reader errors
    READER_UNSUPPORTED_FORMAT = "READER_UNSUPPORTED_FORMAT"
    READER_PARSE_FAILED = "READER_PARSE_FAILED"

    # * Extraction errors
    EXTRACTION_LLM_FAILED = "EXTRACTION_LLM_FAILED"
    EXTRACTION_HEADER_DETECTION_FAILED = "EXTRACTION_HEADER_DETECTION_FAILED"
    EXTRACTION_SCHEMA_VALIDATION_FAILED = "EXTRACTION_SCHEMA_VALIDATION_FAILED"
    EXTRACTION_OUTPUT_PARSE_FAILED = "EXTRACTION_OUTPUT_PARSE_FAILED"

    # * Codegen errors
    CODEGEN_MAX_RETRIES = "CODEGEN_MAX_RETRIES"
    CODEGEN_SYNTAX_ERROR = "CODEGEN_SYNTAX_ERROR"
    CODEGEN_EXECUTION_FAILED = "CODEGEN_EXECUTION_FAILED"


class XLStructError(Exception):
    """Base exception for all XLStruct errors."""

    def __init__(self, message: str, code: ErrorCode | None = None) -> None:
        super().__init__(message)
        self.code = code


class StorageError(XLStructError):
    """File access error (wraps FileNotFoundError, PermissionError, etc.)."""


class ReaderError(XLStructError):
    """Excel parsing error (wraps openpyxl errors)."""


class ExtractionError(XLStructError):
    """LLM extraction error (wraps Instructor/API errors)."""


class CodegenValidationError(XLStructError):
    """All codegen retry attempts exhausted."""

    def __init__(self, message: str, attempts: "list[Any]", code: ErrorCode | None = None) -> None:
        super().__init__(message, code=code)
        self.attempts = attempts
