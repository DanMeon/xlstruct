"""Tests for token counting utilities."""

from xlstruct._tokens import (
    count_tokens,
    estimate_cells_tokens,
    estimate_row_token_costs,
    estimate_sheet_tokens,
)
from xlstruct.schemas.core import CellData, SheetData


class TestEstimateSheetTokens:
    def test_empty_sheet(self):
        sheet = SheetData(name="empty", row_count=0, col_count=0)
        assert estimate_sheet_tokens(sheet) == 0

    def test_simple_sheet(self, simple_sheet: SheetData):
        tokens = estimate_sheet_tokens(simple_sheet)
        assert tokens > 0
        # ^ 24 cells with short values: should be reasonable
        assert tokens < 1000


class TestSpecialTokenContent:
    """Cell values may contain literal special-token text (e.g. '<|endoftext|>').
    The strict encode() raises on it; estimation must use encode_ordinary and
    never crash on valid spreadsheet content."""

    _SPECIAL = "before <|endoftext|> after"

    def test_count_tokens_does_not_raise(self):
        assert count_tokens(self._SPECIAL) > 0

    def test_estimate_cells_tokens_does_not_raise(self):
        cells = [CellData(row=1, col=1, value="<|endoftext|>")]
        assert estimate_cells_tokens(cells) > 0

    def test_estimate_sheet_tokens_does_not_raise(self):
        sheet = SheetData(
            name="s",
            row_count=1,
            col_count=1,
            cells=[CellData(row=1, col=1, value=self._SPECIAL)],
        )
        assert estimate_sheet_tokens(sheet) > 0

    def test_estimate_row_token_costs_does_not_raise(self):
        rows = [[CellData(row=2, col=1, value=self._SPECIAL)]]
        assert estimate_row_token_costs(rows)[0] > 0
