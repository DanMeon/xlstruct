"""Tests for token counting utilities."""

from xlstruct._tokens import estimate_sheet_tokens
from xlstruct.schemas.core import SheetData


class TestEstimateSheetTokens:
    def test_empty_sheet(self):
        sheet = SheetData(name="empty", row_count=0, col_count=0)
        assert estimate_sheet_tokens(sheet) == 0

    def test_simple_sheet(self, simple_sheet: SheetData):
        tokens = estimate_sheet_tokens(simple_sheet)
        assert tokens > 0
        # ^ 24 cells with short values: should be reasonable
        assert tokens < 1000
