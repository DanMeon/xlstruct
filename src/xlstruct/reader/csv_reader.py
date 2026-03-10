"""CsvReader: CSV file parser producing SheetData.

Uses Python stdlib csv module — no extra dependencies.
CSV has no formulas, merged cells, or multi-sheet support.
"""

import csv
import io

from openpyxl.utils import get_column_letter

from xlstruct.schemas.core import CellData, SheetData, WorkbookData


class CsvReader:
    """Read CSV bytes into WorkbookData (single sheet)."""

    def read(
        self,
        file_bytes: bytes,
        sheet_name: str | None = None,
        *,
        encoding: str = "utf-8",
    ) -> WorkbookData:
        """Parse CSV bytes into WorkbookData.

        Args:
            file_bytes: Raw bytes of the CSV file.
            sheet_name: Ignored for CSV (always single sheet).
            encoding: Text encoding. Defaults to utf-8.

        Returns:
            WorkbookData with a single SheetData entry named "Sheet1".
        """
        text = file_bytes.decode(encoding)
        reader = csv.reader(io.StringIO(text))

        cells: list[CellData] = []
        row_count = 0
        col_count = 0

        for r_idx, row in enumerate(reader, start=1):
            row_count = r_idx
            if len(row) > col_count:
                col_count = len(row)

            for c_idx, value in enumerate(row, start=1):
                # ^ Skip empty cells
                if not value:
                    continue

                # ^ Infer numeric types
                parsed = self._parse_value(value)
                data_type = self._infer_type(parsed)

                cells.append(
                    CellData(
                        row=r_idx,
                        col=c_idx,
                        value=parsed,
                        cached_value=parsed,
                        data_type=data_type,
                    )
                )

        dimensions = ""
        if row_count > 0 and col_count > 0:
            dimensions = f"A1:{get_column_letter(col_count)}{row_count}"

        sheet = SheetData(
            name="Sheet1",
            dimensions=dimensions,
            cells=cells,
            row_count=row_count,
            col_count=col_count,
        )
        return WorkbookData(sheets=[sheet])

    @staticmethod
    def _parse_value(raw: str) -> str | int | float | bool:
        """Try to parse a CSV string value into a native Python type."""
        stripped = raw.strip()

        # ^ Boolean
        if stripped.lower() in ("true", "false"):
            return stripped.lower() == "true"

        # ^ Integer
        try:
            return int(stripped)
        except ValueError:
            pass

        # ^ Float
        try:
            return float(stripped)
        except ValueError:
            pass

        return raw

    @staticmethod
    def _infer_type(value: str | int | float | bool) -> str:
        """Map parsed value to data_type code."""
        if isinstance(value, bool):
            return "b"
        if isinstance(value, (int, float)):
            return "n"
        return "s"
