"""Reader contracts: ReaderOptions and the WorkbookReader dispatch protocol."""

from typing import Protocol

from pydantic import BaseModel

from xlstruct.schemas.core import WorkbookData


class ReaderOptions(BaseModel):
    """Options shared across reader implementations.

    Each reader uses only the subset it understands: HybridReader reads
    ``strict_formulas`` / ``evaluate_formulas``; CsvReader reads ``csv_encoding``.
    The unused fields are simply ignored by the other reader.
    """

    strict_formulas: bool = True
    evaluate_formulas: bool = False
    csv_encoding: str = "utf-8"


class WorkbookReader(Protocol):
    """Contract for READER_REGISTRY entries: bytes + options → WorkbookData.

    Implementations are sync (calamine/openpyxl are sync). The Extractor and
    MCP server wrap calls with ``asyncio.to_thread()``.
    """

    def __call__(
        self,
        file_bytes: bytes,
        sheet_name: str | None,
        source_ext: str,
        options: ReaderOptions,
    ) -> WorkbookData:
        """Read raw file bytes into WorkbookData.

        Args:
            file_bytes: Raw bytes of the file.
            sheet_name: If provided, read only this sheet. None = all sheets.
            source_ext: Resolved file extension (e.g. ".xlsx", ".csv").
            options: Reader options (formula handling, CSV encoding).
        """
        ...
