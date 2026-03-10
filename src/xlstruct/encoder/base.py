"""SheetEncoder protocol definition."""

from typing import Protocol

from xlstruct.schemas.core import SheetData


class SheetEncoder(Protocol):
    """Protocol for encoding SheetData into LLM-consumable text."""

    def encode(self, sheet: SheetData, header_rows: list[int] | None = None) -> str:
        """Encode a sheet into text suitable for LLM consumption.

        Args:
            sheet: The sheet data to encode.
            header_rows: If provided, use these rows as headers (skip auto-detection).

        Returns:
            Encoded text string.
        """
        ...
