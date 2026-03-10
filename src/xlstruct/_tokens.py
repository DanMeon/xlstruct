"""Token counting utilities.

Uses tiktoken cl100k_base as a universal approximation for all providers.
Not billing-accurate, but sufficient for strategy selection and budget checks.
"""

from typing import TYPE_CHECKING

import tiktoken

if TYPE_CHECKING:
    from xlstruct.schemas.core import SheetData

# * Module-level singleton
_ENCODING: tiktoken.Encoding | None = None


def _get_encoding() -> tiktoken.Encoding:
    global _ENCODING
    if _ENCODING is None:
        _ENCODING = tiktoken.get_encoding("cl100k_base")
    return _ENCODING


def estimate_sheet_tokens(sheet: "SheetData") -> int:
    """Fast token estimation for a sheet without full encoding.

    Approximation: avg tokens per cell value * cell count + overhead.
    Used for encoder strategy selection (exact count not needed).
    """
    if not sheet.cells:
        return 0

    # ^ Sample up to 50 cells for average token count
    sample_size = min(50, len(sheet.cells))
    sample = sheet.cells[:sample_size]

    total_sample_tokens = 0
    enc = _get_encoding()
    for cell in sample:
        val = str(cell.display_value) if cell.display_value is not None else ""
        total_sample_tokens += len(enc.encode(val))

    avg_tokens_per_cell = total_sample_tokens / sample_size
    estimated = int(avg_tokens_per_cell * len(sheet.cells))

    # ^ Add overhead for formatting (headers, separators, metadata)
    overhead_factor = 1.3
    return int(estimated * overhead_factor)
