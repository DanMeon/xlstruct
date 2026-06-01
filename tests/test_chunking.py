"""Tests for extraction/chunking.py: needs_chunking() and ChunkSplitter."""

from xlstruct._tokens import estimate_row_token_costs, estimate_sheet_tokens
from xlstruct.extraction.chunking import (
    _CHUNKING_ROW_THRESHOLD,
    _MIN_CHUNK_ROWS,
    ChunkSplitter,
    needs_chunking,
)
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
            assert "rows" in chunk.name, f"Chunk name '{chunk.name}' does not contain row range"

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


# * Custom threshold tests


class TestCustomThresholds:
    def test_needs_chunking_custom_row_threshold_triggers(self):
        # ^ 60 data rows = row_count 61, above custom threshold of 50
        sheet = _make_large_sheet(60)
        assert needs_chunking(sheet, token_budget=100_000, row_threshold=50) is True

    def test_needs_chunking_custom_row_threshold_no_trigger(self):
        # ^ 60 data rows = row_count 61, below default threshold of 100
        sheet = _make_large_sheet(60)
        assert needs_chunking(sheet, token_budget=100_000) is False

    def test_split_custom_min_chunk_rows_produces_smaller_chunks(self):
        sheet = _make_large_sheet(250)
        splitter = ChunkSplitter()
        # ^ Default min_chunk_rows=10, row_threshold=100 produces chunks of 100 rows
        default_chunks = splitter.split(sheet, token_budget=500_000)
        # ^ Custom min_chunk_rows=5, row_threshold=50 produces chunks of 50 rows
        custom_chunks = splitter.split(
            sheet, token_budget=500_000, min_chunk_rows=5, row_threshold=50
        )
        assert len(custom_chunks) > len(default_chunks)

    def test_split_custom_thresholds_preserves_all_data(self):
        data_rows = 120
        sheet = _make_large_sheet(data_rows)
        splitter = ChunkSplitter()
        chunks = splitter.split(sheet, token_budget=500_000, min_chunk_rows=5, row_threshold=30)

        # ^ All data rows must be covered
        seen_rows: set[int] = set()
        for chunk in chunks:
            for cell in chunk.cells:
                if cell.row != 1:
                    seen_rows.add(cell.row)

        expected_rows = set(range(2, data_rows + 2))
        assert seen_rows == expected_rows

    def test_split_custom_thresholds_headers_in_every_chunk(self):
        sheet = _make_large_sheet(120)
        splitter = ChunkSplitter()
        chunks = splitter.split(sheet, token_budget=500_000, min_chunk_rows=5, row_threshold=30)
        for chunk in chunks:
            header_cells = [c for c in chunk.cells if c.row == 1]
            assert len(header_cells) > 0


# * Token-based chunking (header-aware budgeting)


def _make_uniform_sheet(cols: int, data_rows: int) -> SheetData:
    """Header + data rows where every data cell is a constant-width value.

    Flat per-row density makes the chunk count predictable, so this fixture is
    used to check that the row_threshold accuracy cap still bounds chunk size
    even when the token budget is loose.
    """
    cells = [CellData(row=1, col=c, value=f"Col{c}", data_type="s") for c in range(1, cols + 1)]
    for r in range(2, data_rows + 2):
        for c in range(1, cols + 1):
            cells.append(CellData(row=r, col=c, value="val", data_type="s"))
    return SheetData(
        name="Uniform",
        dimensions=f"A1:Z{data_rows + 1}",
        cells=cells,
        merged_ranges=[],
        row_count=data_rows + 1,
        col_count=cols,
    )


def _make_header_heavy_sheet(cols: int, data_rows: int) -> SheetData:
    """Long-label header row over compact numeric data — the header is a large
    fraction of a small token budget, so reserving it per chunk matters."""
    label = "Very Long Descriptive Column Header Label Number {c} With Extra Words"
    cells = [
        CellData(row=1, col=c, value=label.format(c=c), data_type="s") for c in range(1, cols + 1)
    ]
    for r in range(2, data_rows + 2):
        for c in range(1, cols + 1):
            cells.append(CellData(row=r, col=c, value=(r * c) % 9, data_type="n"))
    return SheetData(
        name="HeaderHeavy",
        dimensions=f"A1:Z{data_rows + 1}",
        cells=cells,
        merged_ranges=[],
        row_count=data_rows + 1,
        col_count=cols,
    )


def _make_heavy_tail_sheet(cols: int, light_rows: int, heavy_rows: int) -> SheetData:
    """Light rows followed by much heavier rows.

    A proportional (equal-row-count) split groups the heavy tail into one
    over-budget chunk; greedy packing by real per-row cost keeps every chunk
    within budget. This is the case the old sampled-total formula got wrong.
    """
    heavy = "lorem ipsum dolor sit amet consectetur adipiscing elit sed"
    cells = [CellData(row=1, col=c, value=f"Col{c}", data_type="s") for c in range(1, cols + 1)]
    row = 2
    for _ in range(light_rows):
        for c in range(1, cols + 1):
            cells.append(CellData(row=row, col=c, value="x", data_type="s"))
        row += 1
    for _ in range(heavy_rows):
        for c in range(1, cols + 1):
            cells.append(CellData(row=row, col=c, value=heavy, data_type="s"))
        row += 1
    return SheetData(
        name="HeavyTail",
        dimensions=f"A1:Z{row - 1}",
        cells=cells,
        merged_ranges=[],
        row_count=row - 1,
        col_count=cols,
    )


def _exact_chunk_cost(chunk: SheetData, header_row_count: int = 1) -> int:
    """The splitter's own cost model for a chunk: header cost + sum of row costs."""
    header_cells = [c for c in chunk.cells if c.row <= header_row_count]
    rows: dict[int, list[CellData]] = {}
    for c in chunk.cells:
        if c.row > header_row_count:
            rows.setdefault(c.row, []).append(c)
    groups = [header_cells, *(rows[r] for r in sorted(rows))]
    return sum(estimate_row_token_costs(groups))


def _naive_proportional_max_cost(sheet: SheetData, budget: int, header_row_count: int = 1) -> int:
    """Worst chunk cost under the pre-fix proportional split (equal row counts,
    sampled total, no header reservation). Shows the old approach overflowed."""
    header_cells = [c for c in sheet.cells if c.row <= header_row_count]
    data_rows: dict[int, list[CellData]] = {}
    for c in sheet.cells:
        if c.row > header_row_count:
            data_rows.setdefault(c.row, []).append(c)
    srn = sorted(data_rows)
    total = estimate_sheet_tokens(sheet)
    if total > budget:
        chunk_count = max(1, total // budget)
        rows_per_chunk = max(_MIN_CHUNK_ROWS, len(srn) // chunk_count)
    else:
        rows_per_chunk = max(_MIN_CHUNK_ROWS, _CHUNKING_ROW_THRESHOLD)
    header_cost = estimate_row_token_costs([header_cells])[0]
    worst = 0
    for i in range(0, len(srn), rows_per_chunk):
        group = srn[i : i + rows_per_chunk]
        cost = header_cost + sum(estimate_row_token_costs([data_rows[r] for r in group]))
        worst = max(worst, cost)
    return worst


class TestTokenBasedChunking:
    """The token-based path sizes chunks by each row's real token cost (greedy
    bin-packing, header reserved) so every chunk fits token_budget regardless of
    where the heavy rows sit — the case the old proportional split got wrong."""

    def test_header_aware_chunks_fit_budget(self):
        sheet = _make_header_heavy_sheet(cols=20, data_rows=200)
        budget = 2_000
        assert estimate_sheet_tokens(sheet) > budget  # ^ token path is exercised

        chunks = ChunkSplitter().split(sheet, token_budget=budget)
        assert len(chunks) > 1
        for chunk in chunks:
            assert _exact_chunk_cost(chunk) <= budget, f"{chunk.name} exceeds budget"
        # ^ The pre-fix proportional split (no header reservation) overflowed here
        assert _naive_proportional_max_cost(sheet, budget) > budget

    def test_variable_row_sizes_each_chunk_fits(self):
        # ^ Heavy rows concentrated in the tail: a proportional split overflows
        # ^ the tail chunk; greedy packing by real cost keeps every chunk in budget.
        sheet = _make_heavy_tail_sheet(cols=8, light_rows=300, heavy_rows=100)
        budget = 2_000

        chunks = ChunkSplitter().split(sheet, token_budget=budget)
        assert len(chunks) > 1
        for chunk in chunks:
            assert _exact_chunk_cost(chunk) <= budget, f"{chunk.name} exceeds budget"
        # ^ Prove the fix matters: the old proportional split overflows here
        assert _naive_proportional_max_cost(sheet, budget) > budget

    def test_row_threshold_caps_chunk_row_count(self):
        # ^ Even with a loose budget, the accuracy cap bounds each chunk's rows
        sheet = _make_uniform_sheet(cols=10, data_rows=500)
        chunks = ChunkSplitter().split(sheet, token_budget=3_000)
        assert len(chunks) > 1
        for chunk in chunks:
            data_row_nums = {c.row for c in chunk.cells if c.row != 1}
            assert len(data_row_nums) <= _CHUNKING_ROW_THRESHOLD

    def test_token_branch_covers_all_data_rows(self):
        # ^ No data loss: every data row appears in exactly one chunk
        sheet = _make_heavy_tail_sheet(cols=8, light_rows=300, heavy_rows=100)
        chunks = ChunkSplitter().split(sheet, token_budget=2_000)
        all_rows: list[int] = []
        for chunk in chunks:
            rows = {c.row for c in chunk.cells if c.row != 1}
            all_rows.extend(rows)
        # ^ rows 2..401 each appear exactly once (sorted equality rules out dupes)
        assert sorted(all_rows) == list(range(2, 402))

    def test_header_in_every_chunk_token_branch(self):
        sheet = _make_heavy_tail_sheet(cols=8, light_rows=300, heavy_rows=100)
        chunks = ChunkSplitter().split(sheet, token_budget=2_000)
        for chunk in chunks:
            assert any(c.row == 1 for c in chunk.cells), f"{chunk.name} missing header"
