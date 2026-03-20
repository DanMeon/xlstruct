"""Batch extraction result models."""

from collections.abc import Iterator
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

from xlstruct.schemas.usage import TokenUsage

T = TypeVar("T", bound=BaseModel)


class FileResult(BaseModel, Generic[T]):
    """Extraction result for a single file in a batch."""

    source: str = Field(description="File path or URL that was processed")
    success: bool = Field(description="Whether extraction succeeded")
    records: list[T] = Field(default_factory=list, description="Extracted records")
    error: str | None = Field(default=None, description="Error message if extraction failed")
    usage: TokenUsage | None = Field(default=None, description="Token usage for this file")


class BatchResult(BaseModel, Generic[T]):
    """Aggregated result of a batch extraction."""

    results: list[FileResult[T]] = Field(default_factory=list)

    @property
    def succeeded(self) -> int:
        """Number of files that were successfully extracted."""
        return sum(1 for r in self.results if r.success)

    @property
    def failed(self) -> int:
        """Number of files that failed extraction."""
        return sum(1 for r in self.results if not r.success)

    @property
    def total(self) -> int:
        """Total number of files in the batch."""
        return len(self.results)

    @property
    def total_usage(self) -> TokenUsage:
        """Aggregate token usage across all successful files."""
        total = TokenUsage()
        for r in self.results:
            if r.usage is not None:
                total = total + r.usage
        return total

    @property
    def all_records(self) -> list[T]:
        """Flat list of all records from successful files."""
        items: list[T] = []
        for r in self.results:
            if r.success:
                items.extend(r.records)
        return items

    def __iter__(self) -> Iterator[FileResult[T]]:  # type: ignore[override]
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)

    def __getitem__(self, index: int) -> FileResult[T]:
        return self.results[index]
