"""Token counting utilities.

Uses tiktoken cl100k_base as a universal approximation for all providers.
Not billing-accurate, but sufficient for strategy selection and budget checks.
"""

from typing import TYPE_CHECKING

import tiktoken

if TYPE_CHECKING:
    from xlstruct.schemas.core import CellData, SheetData

# * Module-level singleton
_encoding: tiktoken.Encoding | None = None


def _get_encoding() -> tiktoken.Encoding:
    global _encoding
    if _encoding is None:
        _encoding = tiktoken.get_encoding("cl100k_base")
    return _encoding


def count_tokens(text: str) -> int:
    """Count tokens in a text string."""
    # ^ encode_ordinary, not encode: cell content may contain literal special-token
    # ^ text (e.g. "<|endoftext|>"), which the strict encode() raises on.
    return len(_get_encoding().encode_ordinary(text))


# ^ Formatting overhead applied to raw cell tokens (markdown pipes, separators,
# ^ row numbers, metadata). Shared by the sheet estimate and per-row costing.
_OVERHEAD_FACTOR = 1.3


def estimate_cells_tokens(cells: list["CellData"]) -> int:
    """Fast, rough token estimation for an arbitrary list of cells.

    Samples up to 50 cells spread evenly across the list (not just the first 50)
    for an average per-cell token count, scales to the full list, and adds a
    formatting-overhead factor. Even spacing keeps the estimate representative
    when token density varies by position. This is the chunking *gate*; chunk
    sizing itself uses exact per-row costs (see estimate_row_token_costs).
    """
    n = len(cells)
    if n == 0:
        return 0

    # ^ Stratified sample: evenly spaced indices across [0, n) so a dense head
    # ^ or dense tail does not skew the average (a plain cells[:50] would).
    sample_size = min(50, n)
    enc = _get_encoding()
    total_sample_tokens = 0
    for i in range(sample_size):
        cell = cells[i * n // sample_size]
        val = str(cell.display_value) if cell.display_value is not None else ""
        # ^ encode_ordinary: never raise on cell content resembling a special token
        total_sample_tokens += len(enc.encode_ordinary(val))

    avg_tokens_per_cell = total_sample_tokens / sample_size
    estimated = int(avg_tokens_per_cell * n)
    return int(estimated * _OVERHEAD_FACTOR)


def estimate_row_token_costs(rows_cells: list[list["CellData"]]) -> list[int]:
    """Per-row token cost (row tokens x overhead) for chunk sizing.

    Joins each row's cell values and encodes one string per row in a single
    batch. Unlike estimate_cells_tokens this samples nothing: chunk sizing must
    reflect each row's real weight, not a sheet-wide average, so a chunk packed
    to the budget fits regardless of where the heavy rows sit. Encoding per
    joined row (not per cell) is ~17x faster on large sheets — far fewer, longer
    strings — and the join mirrors how cells render with separators, so the
    count tracks the per-cell sum closely.
    """
    if not rows_cells:
        return []

    enc = _get_encoding()
    joined = [
        " ".join(str(c.display_value) if c.display_value is not None else "" for c in cells)
        for cells in rows_cells
    ]
    return [int(len(tokens) * _OVERHEAD_FACTOR) for tokens in enc.encode_ordinary_batch(joined)]


def estimate_sheet_tokens(sheet: "SheetData") -> int:
    """Fast token estimation for a sheet without full encoding.

    Approximation: avg tokens per cell value * cell count + overhead.
    Used for encoder strategy selection (exact count not needed).
    """
    return estimate_cells_tokens(sheet.cells)
