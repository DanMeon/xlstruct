"""Core data models for XLStruct pipeline."""

from collections.abc import Iterator

from pydantic import BaseModel, Field, PrivateAttr


class CellData(BaseModel):
    """Single cell metadata from an Excel worksheet."""

    row: int
    col: int
    value: str | int | float | bool | None = None
    formula: str | None = None
    cached_value: str | int | float | bool | None = None
    data_type: str = "n"
    number_format: str | None = None
    is_merged: bool = False
    merge_range: str | None = None
    merge_origin: tuple[int, int] | None = None

    @property
    def display_value(self) -> str | int | float | bool | None:
        """Best available value: cached_value if present, else value."""
        if self.cached_value is not None:
            return self.cached_value
        return self.value


class SheetData(BaseModel):
    """Single worksheet data."""

    name: str
    dimensions: str = ""
    cells: list[CellData] = Field(default_factory=list)
    merged_ranges: list[str] = Field(default_factory=list)
    row_count: int = 0
    col_count: int = 0

    model_config = {"arbitrary_types_allowed": True}

    # ^ Private cache for O(1) cell lookup, built lazily
    _cell_map: dict[tuple[int, int], CellData] | None = PrivateAttr(default=None)

    def _ensure_cell_map(self) -> dict[tuple[int, int], CellData]:
        if self._cell_map is None:
            self._cell_map = {(c.row, c.col): c for c in self.cells}
        return self._cell_map

    def get_cell(self, row: int, col: int) -> CellData | None:
        """Look up cell by (row, col). O(1) after first call."""
        return self._ensure_cell_map().get((row, col))

    def iter_rows(self) -> Iterator[list[CellData]]:
        """Iterate cells grouped by row number."""
        if not self.cells:
            return
        rows: dict[int, list[CellData]] = {}
        for cell in self.cells:
            rows.setdefault(cell.row, []).append(cell)
        for row_num in sorted(rows):
            yield sorted(rows[row_num], key=lambda c: c.col)


class WorkbookData(BaseModel):
    """Full workbook data."""

    sheets: list[SheetData] = Field(default_factory=list)
    file_name: str = ""
    file_size: int | None = None

    def get_sheet(self, name: str) -> SheetData | None:
        """Look up sheet by name."""
        for sheet in self.sheets:
            if sheet.name == name:
                return sheet
        return None

    @property
    def sheet_names(self) -> list[str]:
        return [s.name for s in self.sheets]
