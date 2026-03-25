"""User-level prompt builder for LLM extraction."""

# * Template segments
_INSTRUCTIONS_SECTION = "## Additional Instructions\n{instructions}\n"

_SAMPLE_NOTE = (
    "## Note\n"
    "The data below shows a **sample** of rows from a larger sheet "
    "(total: {total_rows} rows). Extract records from the sample rows shown.\n"
)

_TASK_SECTION = (
    "\n## Task\n"
    "Extract all matching records from the spreadsheet data above "
    "into the requested schema. Return the complete list."
)

_PROVENANCE_SECTION = (
    "\n## Row Provenance\n"
    "For each extracted record, include a `source_rows` field containing "
    "the 1-indexed Excel row number(s) that the record was extracted from. "
    "Use the Row column in the data table above to determine the row numbers.\n"
)

_CELL_PROVENANCE_SECTION = (
    "\n## Cell Provenance\n"
    "For each extracted record, include a `source_cells` field: a JSON object mapping "
    'each output field name to the cell address (e.g. "A5", "C14") it was extracted from. '
    "Use the column letters from the table header and the Row column for row numbers. "
    'Example: {"name": "A5", "amount": "C5", "date": "D5"}. '
    "If a field is derived from multiple cells or not directly from a single cell, "
    "use the most relevant cell address.\n"
)

_CONFIDENCE_SECTION = (
    "\n## Field Confidence\n"
    "For each field in every extracted record, assess your confidence level "
    "and populate the corresponding `<field>_confidence` field:\n"
    "- **very_high**: Certain — value is clearly and unambiguously present in the cell data.\n"
    "- **high**: Strong inference — value is very likely correct based on context.\n"
    "- **moderate**: Reasonable guess — value requires some interpretation or assumption.\n"
    "- **low**: Uncertain — limited evidence, value could be wrong.\n"
    "- **very_low**: Mostly guessing — very little supporting data.\n"
)


def build_extraction_prompt(
    encoded_sheet: str,
    instructions: str | None = None,
    *,
    is_sampled: bool = False,
    total_rows: int | None = None,
    track_provenance: bool = False,
    include_confidence: bool = False,
) -> str:
    """Build the user prompt for extraction.

    Args:
        encoded_sheet: The encoded spreadsheet text (from any encoder).
        instructions: Optional natural-language hints from the user.
        is_sampled: Whether the data is a sample from a larger sheet.
        total_rows: Total row count of the original sheet (shown when sampled).
        track_provenance: Whether to instruct the LLM to include source row numbers.
        include_confidence: Whether to instruct the LLM to include per-field confidence.
    """
    parts: list[str] = []

    if instructions:
        parts.append(_INSTRUCTIONS_SECTION.format(instructions=instructions))

    if is_sampled and total_rows is not None:
        parts.append(_SAMPLE_NOTE.format(total_rows=total_rows))

    parts.append(f"## Spreadsheet Data\n\n{encoded_sheet}")
    parts.append(_TASK_SECTION)

    if track_provenance:
        parts.append(_PROVENANCE_SECTION)
        parts.append(_CELL_PROVENANCE_SECTION)

    if include_confidence:
        parts.append(_CONFIDENCE_SECTION)

    return "\n".join(parts)
