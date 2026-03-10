"""ExcelReader protocol definition."""

from typing import Protocol

from xlstruct.schemas.core import WorkbookData


class ExcelReader(Protocol):
    """Protocol for reading Excel files into WorkbookData.

    Implementations are sync (openpyxl is sync).
    The Extractor layer wraps calls with asyncio.to_thread().
    """

    def read(
        self,
        file_bytes: bytes,
        sheet_name: str | None = None,
        *,
        source_ext: str = ".xlsx",
    ) -> WorkbookData:
        """Read an Excel file from bytes.

        Args:
            file_bytes: Raw bytes of the .xlsx file.
            sheet_name: If provided, read only this sheet. None = all sheets.
            source_ext: File extension hint for format detection.

        Returns:
            WorkbookData with one or more SheetData entries.
        """
        ...
