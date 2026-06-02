"""Reader dispatch shared by Extractor and the MCP server.

A single ``READER_REGISTRY`` maps a source extension to a reader adapter, so a
new format is added in one place instead of being branched on at every call
site. Replaces the duplicated ``.csv`` / else if-blocks that previously lived in
both extractor.py and mcp_server.py (the latter via private-member access).
"""

from xlstruct.exceptions import ErrorCode, ReaderError
from xlstruct.reader.base import ReaderOptions, WorkbookReader
from xlstruct.reader.csv_reader import CsvReader
from xlstruct.reader.hybrid_reader import HybridReader
from xlstruct.schemas.core import WorkbookData


def _read_csv(
    file_bytes: bytes,
    sheet_name: str | None,
    source_ext: str,
    options: ReaderOptions,
) -> WorkbookData:
    return CsvReader().read(file_bytes, sheet_name, encoding=options.csv_encoding)


def _read_hybrid(
    file_bytes: bytes,
    sheet_name: str | None,
    source_ext: str,
    options: ReaderOptions,
) -> WorkbookData:
    return HybridReader().read(
        file_bytes,
        sheet_name,
        source_ext=source_ext,
        strict_formulas=options.strict_formulas,
        evaluate_formulas=options.evaluate_formulas,
    )


READER_REGISTRY: dict[str, WorkbookReader] = {
    ".csv": _read_csv,
    ".xlsx": _read_hybrid,
    ".xlsm": _read_hybrid,
    ".xltx": _read_hybrid,
    ".xltm": _read_hybrid,
    ".xls": _read_hybrid,
}


def get_source_ext(source: str) -> str:
    """Extract and validate the file extension from a source path/URL."""
    lower = source.lower().rsplit("?", 1)[0]  # ^ Strip query params for URLs
    # ^ Longest extension first so a future suffix-overlapping format can't shadow
    for ext in sorted(READER_REGISTRY, key=len, reverse=True):
        if lower.endswith(ext):
            return ext
    raise ReaderError(
        f"Unsupported file format: {source}",
        code=ErrorCode.READER_UNSUPPORTED_FORMAT,
    )


def read_workbook(
    file_bytes: bytes,
    source: str,
    sheet_name: str | None = None,
    *,
    options: ReaderOptions | None = None,
) -> WorkbookData:
    """Dispatch raw file bytes to the right reader by source extension.

    Synchronous — callers wrap this in ``asyncio.to_thread()``.

    Args:
        file_bytes: Raw bytes of the file.
        source: File path or URL (used only to resolve the extension).
        sheet_name: If provided, read only this sheet. None = all sheets.
        options: Reader options. Defaults to ``ReaderOptions()`` when omitted.
    """
    ext = get_source_ext(source)
    reader = READER_REGISTRY[ext]
    return reader(file_bytes, sheet_name, ext, options or ReaderOptions())
