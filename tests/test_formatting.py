"""Tests for shared encoder formatting utilities."""

from xlstruct.encoder._formatting import (
    build_column_headers,
    detect_header_row,
    find_non_empty_rows,
    format_cell_value,
    format_merged_regions,
    summarize_column_types,
)
from xlstruct.schemas.core import CellData, SheetData


class TestFormatCellValue:
    def test_none(self):
        cell = CellData(row=1, col=1)
        assert format_cell_value(cell) == ""

    def test_string(self):
        cell = CellData(row=1, col=1, value="hello")
        assert format_cell_value(cell) == "hello"

    def test_int(self):
        cell = CellData(row=1, col=1, value=42)
        assert format_cell_value(cell) == "42"

    def test_float_clean(self):
        cell = CellData(row=1, col=1, value=250.0)
        assert format_cell_value(cell) == "250"

    def test_float_with_decimals(self):
        cell = CellData(row=1, col=1, value=42.5)
        assert format_cell_value(cell) == "42.5"

    def test_bool_true(self):
        cell = CellData(row=1, col=1, value=True)
        assert format_cell_value(cell) == "TRUE"

    def test_bool_false(self):
        cell = CellData(row=1, col=1, value=False)
        assert format_cell_value(cell) == "FALSE"

    def test_cached_value_preferred(self):
        cell = CellData(row=1, col=1, value="=SUM(A1:A10)", cached_value=100.0)
        assert format_cell_value(cell) == "100"


class TestDetectHeaderRow:
    def test_simple_sheet(self, simple_sheet: SheetData):
        row = detect_header_row(simple_sheet)
        assert row == 1

    def test_merged_sheet(self, merged_sheet: SheetData):
        row = detect_header_row(merged_sheet)
        assert row == 2  # ^ After merged title in row 1

    def test_empty_sheet(self):
        sheet = SheetData(name="empty", row_count=0, col_count=0)
        assert detect_header_row(sheet) is None


class TestBuildColumnHeaders:
    def test_simple(self, simple_sheet: SheetData):
        headers = build_column_headers(simple_sheet, 1)
        assert headers[1] == "Item"
        assert headers[2] == "Description"
        assert headers[3] == "Qty"
        assert headers[4] == "Price"


class TestFormatMergedRegions:
    def test_with_value(self, merged_sheet: SheetData):
        regions = format_merged_regions(merged_sheet)
        assert len(regions) == 1
        assert "Invoice #2024-001" in regions[0]


class TestFindNonEmptyRows:
    def test_all_rows_non_empty(self, simple_sheet: SheetData):
        non_empty = find_non_empty_rows(simple_sheet)
        # ^ All rows 1-6 have data
        assert non_empty == {1, 2, 3, 4, 5, 6}

    def test_skips_empty_rows(self):
        # ^ Rows 1 and 3 hold data; row 2 is a gap that must be excluded
        cells = [
            CellData(row=1, col=1, value="a"),
            CellData(row=3, col=1, value="b"),
        ]
        sheet = SheetData(name="gap", row_count=3, col_count=1, cells=cells)
        non_empty = find_non_empty_rows(sheet)
        assert non_empty == {1, 3}
        assert 2 not in non_empty


class TestSummarizeColumnTypes:
    def test_simple(self, simple_sheet: SheetData):
        types = summarize_column_types(simple_sheet, header_row=1)
        assert types[1] == "str"
        assert types[2] == "str"
        assert types[3] == "int"
        assert types[4] == "float"
