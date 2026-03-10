"""Tests for core data models."""

from xlstruct.schemas.core import CellData, SheetData, WorkbookData


class TestCellData:
    def test_display_value_prefers_cached(self):
        cell = CellData(row=1, col=1, value="=SUM(A1:A10)", cached_value=42.0)
        assert cell.display_value == 42.0

    def test_display_value_falls_back_to_value(self):
        cell = CellData(row=1, col=1, value="hello")
        assert cell.display_value == "hello"

    def test_display_value_none_when_both_none(self):
        cell = CellData(row=1, col=1)
        assert cell.display_value is None


class TestSheetData:
    def test_get_cell(self, simple_sheet: SheetData):
        cell = simple_sheet.get_cell(1, 1)
        assert cell is not None
        assert cell.value == "Item"

    def test_get_cell_not_found(self, simple_sheet: SheetData):
        assert simple_sheet.get_cell(999, 999) is None

    def test_iter_rows(self, simple_sheet: SheetData):
        rows = list(simple_sheet.iter_rows())
        assert len(rows) == 6  # 1 header + 5 data
        assert rows[0][0].value == "Item"
        assert rows[1][0].value == "WDG-001"


class TestWorkbookData:
    def test_get_sheet(self):
        wb = WorkbookData(
            sheets=[
                SheetData(name="Sheet1", row_count=1, col_count=1),
                SheetData(name="Sheet2", row_count=1, col_count=1),
            ]
        )
        assert wb.get_sheet("Sheet1") is not None
        assert wb.get_sheet("Sheet2") is not None
        assert wb.get_sheet("Sheet3") is None

    def test_sheet_names(self):
        wb = WorkbookData(
            sheets=[
                SheetData(name="Sales", row_count=0, col_count=0),
                SheetData(name="Inventory", row_count=0, col_count=0),
            ]
        )
        assert wb.sheet_names == ["Sales", "Inventory"]
