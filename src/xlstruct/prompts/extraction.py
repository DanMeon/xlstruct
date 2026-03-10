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


def build_extraction_prompt(
    encoded_sheet: str,
    instructions: str | None = None,
    *,
    is_sampled: bool = False,
    total_rows: int | None = None,
) -> str:
    """Build the user prompt for extraction.

    Args:
        encoded_sheet: The encoded spreadsheet text (from any encoder).
        instructions: Optional natural-language hints from the user.
        is_sampled: Whether the data is a sample from a larger sheet.
        total_rows: Total row count of the original sheet (shown when sampled).
    """
    parts: list[str] = []

    if instructions:
        parts.append(_INSTRUCTIONS_SECTION.format(instructions=instructions))

    if is_sampled and total_rows is not None:
        parts.append(_SAMPLE_NOTE.format(total_rows=total_rows))

    parts.append(f"## Spreadsheet Data\n\n{encoded_sheet}")
    parts.append(_TASK_SECTION)

    return "\n".join(parts)
