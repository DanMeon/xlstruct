"""Tests for extraction/chunking.py: needs_chunking() and ChunkSplitter."""

from xlstruct.extraction.chunking import ChunkSplitter, needs_chunking
from xlstruct.schemas.core import CellData, SheetData

# * Fixtures

def _make_small_sheet() -> SheetData:
    """5 data rows with header — well under any reasonable token budget."""
    cells = [
        CellData(row=1, col=1, value="Name", data_type="s"),
        CellData(row=1, col=2, value="Value", data_type="s"),
    ]
    for i in range(2, 7):  # ^ rows 2-6 (5 data rows)
        cells.append(CellData(row=i, col=1, value=f"item_{i}", data_type="s"))
        cells.append(CellData(row=i, col=2, value=i * 10, data_type="n"))
    return SheetData(
        name="Small",
        dimensions="A1:B6",
        cells=cells,
        merged_ranges=[],
        row_count=6,
        col_count=2,
    )


def _make_large_sheet(data_rows: int) -> SheetData:
    """Sheet with header + data_rows data rows."""
    cells = [
        CellData(row=1, col=1, value="Name", data_type="s"),
        CellData(row=1, col=2, value="Value", data_type="s"),
    ]
    for i in range(2, data_rows + 2):
        cells.append(CellData(row=i, col=1, value=f"item_{i}", data_type="s"))
        cells.append(CellData(row=i, col=2, value=i * 10, data_type="n"))
    return SheetData(
        name="Large",
        dimensions=f"A1:B{data_rows + 1}",
        cells=cells,
        merged_ranges=[],
        row_count=data_rows + 1,
        col_count=2,
    )


# * needs_chunking

class TestNeedsChunking:
    def test_small_sheet_returns_false(self):
        sheet = _make_small_sheet()
        # ^ 5 rows, tiny token count — far below any threshold
        assert needs_chunking(sheet, token_budget=10_000) is False

    def test_large_sheet_by_row_count_returns_true(self):
        # ^ 200 data rows = row_count 201, well above _CHUNKING_ROW_THRESHOLD=100
        sheet = _make_large_sheet(200)
        assert needs_chunking(sheet, token_budget=100_000) is True

    def test_row_threshold_boundary(self):
        # ^ Exactly at row threshold (row_count=101): should trigger chunking
        sheet = _make_large_sheet(100)
        # ^ row_count == 101 (header + 100 data rows) > 100 threshold
        assert needs_chunking(sheet, token_budget=100_000) is True

    def test_few_rows_but_tiny_budget_triggers_chunking(self):
        sheet = _make_small_sheet()
        # ^ Token budget of 1 forces chunking even for small sheets
        assert needs_chunking(sheet, token_budget=1) is True

    def test_few_rows_large_budget_no_chunking(self):
        sheet = _make_small_sheet()
        assert needs_chunking(sheet, token_budget=500_000) is False


# * ChunkSplitter.split

class TestChunkSplitterSplit:
    def test_empty_sheet_returns_single_item_list(self):
        sheet = SheetData(
            name="Empty",
            dimensions="",
            cells=[],
            merged_ranges=[],
            row_count=0,
            col_count=0,
        )
        splitter = ChunkSplitter()
        result = splitter.split(sheet, token_budget=1_000)
        assert len(result) == 1
        assert result[0] is sheet

    def test_large_sheet_produces_multiple_chunks(self):
        sheet = _make_large_sheet(250)
        splitter = ChunkSplitter()
        # ^ Token budget large enough so splitting is row-based
        chunks = splitter.split(sheet, token_budget=500_000)
        assert len(chunks) > 1

    def test_each_chunk_contains_header_cells(self):
        sheet = _make_large_sheet(250)
        splitter = ChunkSplitter()
        chunks = splitter.split(sheet, token_budget=500_000)
        for chunk in chunks:
            # ^ Header row (row=1) must be present in every chunk
            header_cells_in_chunk = [c for c in chunk.cells if c.row == 1]
            assert len(header_cells_in_chunk) > 0, (
                f"Chunk '{chunk.name}' is missing header row cells"
            )

    def test_chunk_names_contain_row_range(self):
        sheet = _make_large_sheet(250)
        splitter = ChunkSplitter()
        chunks = splitter.split(sheet, token_budget=500_000)
        for chunk in chunks:
            # ^ Name must follow pattern "Large (rows X-Y)"
            assert "rows" in chunk.name, (
                f"Chunk name '{chunk.name}' does not contain row range"
            )

    def test_header_cells_duplicated_in_each_chunk(self):
        sheet = _make_large_sheet(250)
        header_cells = [c for c in sheet.cells if c.row == 1]
        splitter = ChunkSplitter()
        chunks = splitter.split(sheet, token_budget=500_000)

        for chunk in chunks:
            chunk_header = [c for c in chunk.cells if c.row == 1]
            assert len(chunk_header) == len(header_cells), (
                f"Chunk '{chunk.name}' has {len(chunk_header)} header cells, "
                f"expected {len(header_cells)}"
            )

    def test_small_sheet_returns_single_chunk(self):
        sheet = _make_small_sheet()
        splitter = ChunkSplitter()
        chunks = splitter.split(sheet, token_budget=500_000)
        # ^ Small sheet fits in one chunk
        assert len(chunks) == 1

    def test_chunks_cover_all_data_rows(self):
        data_rows = 250
        sheet = _make_large_sheet(data_rows)
        splitter = ChunkSplitter()
        chunks = splitter.split(sheet, token_budget=500_000)

        # ^ Collect all non-header data row numbers across chunks
        seen_rows: set[int] = set()
        for chunk in chunks:
            for cell in chunk.cells:
                if cell.row != 1:  # ^ skip header
                    seen_rows.add(cell.row)

        # ^ Every data row (2 to data_rows+1) must appear in exactly one chunk
        expected_rows = set(range(2, data_rows + 2))
        assert seen_rows == expected_rows

    def test_row_count_251_produces_multiple_chunks(self):
        # ^ Explicit test for the described scenario: 250 data rows, row_count=251
        sheet = _make_large_sheet(250)
        assert sheet.row_count == 251
        splitter = ChunkSplitter()
        chunks = splitter.split(sheet, token_budget=500_000)
        assert len(chunks) > 1
