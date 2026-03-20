"""Pydantic models for the code generation pipeline."""

from pydantic import BaseModel, Field


class HeaderDetectionResult(BaseModel):
    """LLM-detected header row information."""

    header_rows: list[int] = Field(
        description="1-indexed row numbers that form the column header. "
        "For multi-level headers (e.g., category + subcategory), include all rows. "
        "Exclude title rows (full-width merges like report titles)."
    )
    reasoning: str = Field(
        description="Brief explanation of why these rows were identified as headers."
    )


class ColumnMapping(BaseModel):
    """Maps one schema field to its source column(s) in the spreadsheet."""

    schema_field: str = Field(
        description="Pydantic schema field name (e.g. 'product_name', 'unit_price')"
    )
    source_columns: list[str] = Field(
        description="Excel column identifiers this field maps to. "
        "Use combined header labels like 'D(Q1/Revenue)' or 'E(Q1/Cost)'."
    )
    mapping_logic: str = Field(
        description="How to derive the schema field value from the source column(s). "
        "e.g. 'Header contains N → NORTH, S → SOUTH'"
    )


class MappingPlan(BaseModel):
    """LLM-generated structure analysis and column mapping plan."""

    header_structure: str = Field(
        description="Description of the header layout. "
        "e.g. '2-row multi-level header: row 1 = quarter, row 2 = metric'"
    )
    data_start_row: int = Field(
        description="1-indexed row number where data begins (after headers)"
    )
    row_to_records: str = Field(
        description="How rows map to output records. "
        "e.g. '1:1 — each row produces one record' or '1:N — one row unpivots into N records'"
    )
    row_classification: str = Field(
        description="How to distinguish data rows from non-data rows "
        "(group headers, subtotals, empty rows, etc). "
        "Specify which column(s) to check, what pattern to use "
        "(prefix, presence of value, numeric check, etc), and how to "
        "detect group boundaries. "
        "e.g. 'Col C contains location code starting with a region prefix "
        "(North/South). Rows where col B has a value but col C is empty are "
        "group headers — skip them. Use col C prefix to determine the group.'"
    )
    column_mappings: list[ColumnMapping] = Field(
        description="Mapping from each schema field to source column(s)"
    )
    special_handling: list[str] = Field(
        description="Special parsing considerations. "
        "e.g. 'Column A: forward-fill merged cells', 'Skip summary rows'"
    )


class GeneratedScript(BaseModel):
    """LLM-generated transformation script."""

    code: str = Field(description="Complete standalone Python script")
    explanation: str = Field(description="Brief explanation of the transformation logic")


class CodegenAttempt(BaseModel):
    """Records one code generation attempt for debugging."""

    attempt: int
    code: str
    error: str | None = None
