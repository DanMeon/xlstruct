"""ChunkSplitter: Splits large sheets into processable chunks."""

from xlstruct._tokens import estimate_row_token_costs, estimate_sheet_tokens
from xlstruct.schemas.core import CellData, SheetData

# ^ Minimum rows per chunk to avoid degenerate cases
_MIN_CHUNK_ROWS = 10

# ^ Force chunking for sheets with many rows regardless of token count.
# ^ Smaller chunks improve LLM extraction accuracy on large sheets.
_CHUNKING_ROW_THRESHOLD = 100


def needs_chunking(
    sheet: SheetData,
    token_budget: int,
    row_threshold: int = _CHUNKING_ROW_THRESHOLD,
) -> bool:
    """Check if a sheet needs to be split into chunks for extraction."""
    if sheet.row_count > row_threshold:
        return True
    estimated = estimate_sheet_tokens(sheet)
    return estimated > token_budget


class ChunkSplitter:
    """Splits a SheetData into smaller SheetData chunks.

    Each chunk preserves the header row for context.
    Splitting is row-range based.
    """

    def split(
        self,
        sheet: SheetData,
        token_budget: int,
        *,
        min_chunk_rows: int = _MIN_CHUNK_ROWS,
        row_threshold: int = _CHUNKING_ROW_THRESHOLD,
    ) -> list[SheetData]:
        """Split sheet into chunks for extraction.

        Each chunk includes header cells + a range of data rows.
        Chunking is triggered by row count OR token count exceeding limits.
        """
        if not sheet.cells:
            return [sheet]

        # ^ Determine header row
        from xlstruct.encoder._formatting import detect_header_row

        header_row = detect_header_row(sheet)
        data_start = (header_row + 1) if header_row else 1

        # ^ Collect header cells
        header_cells: list[CellData] = []
        if header_row:
            header_cells = [c for c in sheet.cells if c.row <= header_row]

        # ^ Collect data cells grouped by row
        data_rows: dict[int, list[CellData]] = {}
        for cell in sheet.cells:
            if cell.row >= data_start:
                data_rows.setdefault(cell.row, []).append(cell)

        sorted_row_nums = sorted(data_rows.keys())
        if not sorted_row_nums:
            return [sheet]

        # ^ Size chunks by each data row's real token cost (greedy bin-packing),
        # ^ reserving the header cost that every chunk re-includes. This replaces
        # ^ "sampled total / budget assuming uniform rows": chunks are packed to
        # ^ the actual per-row weight, so a chunk fits (token_budget - header)
        # ^ regardless of where the heavy rows sit. The row_threshold accuracy cap
        # ^ still bounds each chunk's row count.
        all_costs = estimate_row_token_costs(
            [header_cells, *(data_rows[rn] for rn in sorted_row_nums)]
        )
        header_cost = all_costs[0]
        row_costs = all_costs[1:]
        data_budget = max(1, token_budget - header_cost)

        row_groups = self._pack_rows(
            sorted_row_nums,
            row_costs,
            data_budget=data_budget,
            min_chunk_rows=min_chunk_rows,
            row_threshold=row_threshold,
        )

        # * Build chunks
        chunks: list[SheetData] = []
        for chunk_row_nums in row_groups:
            chunk_cells = list(header_cells)  # ^ Copy header cells into each chunk
            for rn in chunk_row_nums:
                chunk_cells.extend(data_rows[rn])

            min_data_row = chunk_row_nums[0]
            max_data_row = chunk_row_nums[-1]

            chunks.append(
                SheetData(
                    name=f"{sheet.name} (rows {min_data_row}-{max_data_row})",
                    dimensions=sheet.dimensions,
                    cells=chunk_cells,
                    merged_ranges=sheet.merged_ranges,
                    row_count=len(chunk_row_nums) + (header_row or 0),
                    col_count=sheet.col_count,
                )
            )

        return chunks

    @staticmethod
    def _pack_rows(
        row_nums: list[int],
        row_costs: list[int],
        *,
        data_budget: int,
        min_chunk_rows: int,
        row_threshold: int,
    ) -> list[list[int]]:
        """Greedily group rows into chunks that fit data_budget tokens.

        A chunk closes when it reaches row_threshold rows (the accuracy cap, a
        hard upper bound that takes precedence over min_chunk_rows) or when
        adding the next row would exceed data_budget — but the budget close only
        applies once the chunk already holds min_chunk_rows, so tiny chunks are
        avoided. Two degenerate cases can still exceed the budget, both
        unavoidable: a single row heavier than data_budget (a row cannot be
        split), and the first min_chunk_rows rows when they are collectively
        heavier than data_budget (they are appended before the budget gate
        activates).
        """
        groups: list[list[int]] = []
        current: list[int] = []
        current_cost = 0
        for row_num, cost in zip(row_nums, row_costs):
            over_budget = current_cost + cost > data_budget and len(current) >= min_chunk_rows
            at_row_cap = len(current) >= row_threshold
            if current and (over_budget or at_row_cap):
                groups.append(current)
                current = []
                current_cost = 0
            current.append(row_num)
            current_cost += cost
        if current:
            groups.append(current)
        return groups
