"""ChunkSplitter: Splits large sheets into processable chunks."""

from xlstruct._tokens import estimate_sheet_tokens
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

        # ^ Calculate rows per chunk from both token budget and row threshold
        data_row_count = len(sorted_row_nums)
        total_tokens = estimate_sheet_tokens(sheet)

        if total_tokens > token_budget:
            # ^ Token-based: split proportionally
            chunk_count = max(1, total_tokens // token_budget)
            rows_per_chunk = max(min_chunk_rows, data_row_count // chunk_count)
        else:
            # ^ Row-based: cap at threshold
            rows_per_chunk = max(min_chunk_rows, row_threshold)

        # * Build chunks
        chunks: list[SheetData] = []
        for i in range(0, len(sorted_row_nums), rows_per_chunk):
            chunk_row_nums = sorted_row_nums[i : i + rows_per_chunk]
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
