"""CsvReader: CSV file parser producing SheetData.

Uses Python stdlib csv module — no extra dependencies.
CSV has no formulas, merged cells, or multi-sheet support.
Dialect (delimiter, quoting) is auto-detected via ``csv.Sniffer``.
"""

import csv
import datetime
import io
import logging

from openpyxl.utils import get_column_letter

from xlstruct.schemas.core import CellData, SheetData, WorkbookData

log = logging.getLogger(__name__)

# ^ Sample size for csv.Sniffer — 8 KB covers most header + first rows.
_SNIFF_SAMPLE_BYTES = 8192


class CsvReader:
    """Read CSV bytes into WorkbookData (single sheet)."""

    # * Dialect detection

    @staticmethod
    def _detect_dialect(text: str) -> csv.Dialect | None:
        """Sniff the CSV dialect from the first ~8 KB of *text*.

        Returns:
            Detected ``csv.Dialect`` or ``None`` when detection fails.
        """
        sample = text[:_SNIFF_SAMPLE_BYTES]
        try:
            dialect: csv.Dialect = csv.Sniffer().sniff(sample)  # type: ignore
            return dialect
        except csv.Error:
            return None

    # * Public API

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
        # ^ utf-8-sig strips BOM if present; identical to utf-8 otherwise
        effective_encoding = "utf-8-sig" if encoding == "utf-8" else encoding
        text = file_bytes.decode(effective_encoding)

        # * Dialect auto-detection
        dialect = self._detect_dialect(text)
        if dialect is not None:
            log.debug("CSV dialect detected: delimiter=%r", dialect.delimiter)
            reader = csv.reader(io.StringIO(text), dialect=dialect)
        else:
            log.debug("CSV dialect detection failed — falling back to comma delimiter")
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
    def _is_iso_date(value: str) -> bool:
        """Check if a string is a valid ISO date or datetime."""
        if len(value) < 10:
            return False
        try:
            datetime.datetime.fromisoformat(value)
            return True
        except ValueError:
            pass
        try:
            datetime.date.fromisoformat(value)
            return True
        except ValueError:
            return False

    @staticmethod
    def _infer_type(value: str | int | float | bool) -> str:
        """Map parsed value to data_type code."""
        if isinstance(value, bool):
            return "b"
        if isinstance(value, (int, float)):
            return "n"
        if CsvReader._is_iso_date(value):  # pyright: ignore[reportUnnecessaryIsInstance]
            return "d"
        return "s"
