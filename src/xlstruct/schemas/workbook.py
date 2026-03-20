"""Multi-sheet extraction result models."""

from collections.abc import Iterator
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from xlstruct.schemas.usage import TokenUsage

T = TypeVar("T", bound=BaseModel)


class SheetResult(BaseModel, Generic[T]):
    """Extraction result for a single sheet in a workbook."""

    sheet_name: str = Field(description="Name of the extracted sheet")
    success: bool = Field(description="Whether extraction succeeded")
    records: list[T] = Field(default_factory=list, description="Extracted records")
    error: str | None = Field(default=None, description="Error message if extraction failed")
    usage: TokenUsage | None = Field(default=None, description="Token usage for this sheet")


class WorkbookResult(BaseModel):
    """Aggregated result of multi-sheet extraction."""

    results: dict[str, SheetResult[Any]] = Field(default_factory=dict)

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.results.values() if r.success)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results.values() if not r.success)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def total_usage(self) -> TokenUsage:
        """Aggregate token usage across all successful sheets."""
        total = TokenUsage()
        for r in self.results.values():
            if r.usage is not None:
                total = total + r.usage
        return total

    @property
    def sheet_names(self) -> list[str]:
        return list(self.results.keys())

    def __getitem__(self, sheet_name: str) -> SheetResult[Any]:
        return self.results[sheet_name]

    def __contains__(self, sheet_name: object) -> bool:
        return sheet_name in self.results

    def __iter__(self) -> Iterator[str]:  # type: ignore[override]
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)
