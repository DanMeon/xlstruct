"""Progress tracking models for batch and workbook extraction."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ProgressStatus(StrEnum):
    """Status of a single item in a batch/workbook extraction."""

    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class ProgressEvent(BaseModel):
    """Progress event emitted during batch or workbook extraction.

    Attributes:
        source: File path (batch) or sheet name (workbook) being processed.
        status: Current status of this item.
        completed: Total completed items so far (including failures).
        total: Total number of items in the batch/workbook.
        error: Error message if status is FAILED.
    """

    source: str = Field(description="File path or sheet name")
    status: ProgressStatus = Field(description="Current status")
    completed: int = Field(description="Total completed items so far")
    total: int = Field(description="Total number of items")
    error: str | None = Field(default=None, description="Error message if failed")

    @property
    def progress(self) -> float:
        """Progress as a fraction (0.0 to 1.0)."""
        return self.completed / self.total if self.total > 0 else 0.0


ProgressCallback = Any  # ^ Callable[[ProgressEvent], None] — Any to avoid import complexity
