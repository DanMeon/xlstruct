# pyright: reportUnusedFunction=false
"""MCP server for XLStruct — exposes extraction capabilities as tools.

Requires the 'mcp' optional dependency:
    pip install xlstruct[mcp]
    # or
    uv add xlstruct[mcp]

Run via:
    xlstruct-mcp                        # stdio transport (default)
    uv run xlstruct-mcp                 # from project root
    uv run mcp run src/xlstruct/mcp_server.py
"""

import asyncio
import datetime
import json
import logging
from pathlib import Path as PathLibPath
from typing import Any, Literal

from pydantic import BaseModel, Field, create_model

from xlstruct.codegen.cache import ScriptCache
from xlstruct.config import ExtractionConfig, ExtractionMode
from xlstruct.extractor import ExtractionResult, Extractor
from xlstruct.reader.hybrid_reader import HybridReader
from xlstruct.schemas.batch import BatchResult
from xlstruct.schemas.core import SheetData
from xlstruct.storage import read_file
from xlstruct.suggest import render_schema_source

logger = logging.getLogger(__name__)

# * Type mapping for dynamic Pydantic model creation from JSON schema

TYPE_MAP: dict[str, type] = {
    "str": str,
    "string": str,
    "int": int,
    "integer": int,
    "float": float,
    "number": float,
    "bool": bool,
    "boolean": bool,
    "date": datetime.date,
    "datetime": datetime.datetime,
}


def build_model_from_schema_json(schema_json: str) -> type[BaseModel]:
    """Create a Pydantic model from a JSON schema string.

    Accepts two formats:

    1. Simple field mapping (name -> type):
       ``{"name": "str", "amount": "float", "date": "str"}``

    2. Detailed field definitions (name -> {type, nullable?, description?}):
       ``{"name": {"type": "str"}, "amount": {"type": "float", "nullable": true}}``

    Supported type strings: str, string, int, integer, float, number,
    bool, boolean, date, datetime.

    Args:
        schema_json: JSON string defining field names and types.

    Returns:
        A dynamically created Pydantic model class.

    Raises:
        ValueError: If schema_json is not valid JSON or contains unknown types.
    """
    try:
        raw = json.loads(schema_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in schema_json: {e}") from e

    if not isinstance(raw, dict) or not raw:
        raise ValueError("schema_json must be a non-empty JSON object mapping field names to types")

    field_definitions: dict[str, Any] = {}
    for field_name, field_spec in raw.items():
        # * Normalize to dict form
        if isinstance(field_spec, str):
            field_spec = {"type": field_spec}

        type_str = field_spec.get("type", "str")
        nullable = field_spec.get("nullable", False)
        description = field_spec.get("description")

        # * Resolve python_type based on type_str
        if type_str.lower() == "list":
            items_type_str = field_spec.get("items", "str")
            items_type = TYPE_MAP.get(items_type_str.lower())
            if items_type is None:
                raise ValueError(
                    f"Unknown items type '{items_type_str}' for list field '{field_name}'. "
                    f"Supported: {', '.join(sorted(TYPE_MAP.keys()))}"
                )
            python_type = list[items_type]  # type: ignore[valid-type]
        elif type_str.lower() == "object":
            properties = field_spec.get("properties", {})
            if not properties:
                raise ValueError(f"Object field '{field_name}' requires non-empty 'properties'")
            # ^ Recursive call to build nested model
            nested_schema_json = json.dumps(properties)
            python_type = build_model_from_schema_json(nested_schema_json)  # type: ignore[assignment]
            python_type.__name__ = field_name.title().replace("_", "")
        elif type_str.lower() == "enum":
            values = field_spec.get("values", [])
            if not values:
                raise ValueError(f"Enum field '{field_name}' requires non-empty 'values' list")
            python_type = Literal[tuple(values)]  # type: ignore[valid-type]
        else:
            python_type = TYPE_MAP.get(type_str.lower())
            if python_type is None:
                raise ValueError(
                    f"Unknown type '{type_str}' for field '{field_name}'. "
                    f"Supported: {', '.join(sorted(TYPE_MAP.keys()))}"
                )

        if nullable:
            python_type = python_type | None  # type: ignore

        if description:
            field_definitions[field_name] = (python_type, Field(description=description))
        else:
            field_definitions[field_name] = (python_type, ...)

    return create_model("DynamicSchema", **field_definitions)


def _create_extractor(provider: str | None = None) -> Extractor:
    """Create an Extractor instance with the given provider."""
    if provider:
        return Extractor(provider=provider)
    return Extractor()


def _validate_source(source: str) -> None:
    """Validate that a local file source exists.

    Remote sources (s3://, gs://, az://, http://, https://) are not validated
    locally since they require network access.
    """
    if source.startswith(("s3://", "gs://", "az://", "http://", "https://")):
        return
    path = PathLibPath(source)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {source}")
    if not path.is_file():
        raise ValueError(f"Path is not a file: {source}")


async def _load_sheet(source: str, sheet: str | None = None) -> SheetData:
    """Load a single sheet from a file for inspection."""
    file_bytes = await read_file(source)
    ext = Extractor._get_source_ext(source)  # pyright: ignore[reportPrivateUsage]

    if ext == ".csv":
        from xlstruct.reader.csv_reader import CsvReader

        csv_reader = CsvReader()
        workbook = await asyncio.to_thread(csv_reader.read, file_bytes, sheet)
    else:
        reader = HybridReader()
        workbook = await asyncio.to_thread(reader.read, file_bytes, sheet, source_ext=ext)

    return workbook.sheets[0]


def create_mcp_server() -> Any:
    """Create and configure the FastMCP server with all xlstruct tools.

    Returns:
        A configured FastMCP server instance.
    """
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "xlstruct",
        json_response=True,
    )

    # * Tool: extract

    @mcp.tool()
    async def extract(
        source: str,
        schema_json: str,
        provider: str | None = None,
        sheet: str | None = None,
        mode: str = "auto",
        instructions: str | None = None,
    ) -> str:
        """Extract structured data from an Excel/CSV file.

        Reads the spreadsheet, sends it to an LLM, and returns structured records
        matching the provided schema.

        Args:
            source: File path or URL (local, s3://, az://, gs://).
            schema_json: JSON object defining field names and types.
                Example: {"name": "str", "amount": "float", "date": "str"}
            provider: LLM provider string (e.g. "openai/gpt-4o"). Uses default if omitted.
            sheet: Target sheet name. Uses first sheet if omitted.
            mode: Extraction mode — "auto", "direct", or "codegen".
            instructions: Optional natural-language hints for the LLM.

        Returns:
            JSON string with extracted records and usage statistics.
        """
        _validate_source(source)
        output_schema = build_model_from_schema_json(schema_json)
        extractor = _create_extractor(provider)

        extraction_mode = ExtractionMode(mode)
        config = ExtractionConfig(
            output_schema=output_schema,
            mode=extraction_mode,
            sheet=sheet,
            instructions=instructions,
        )

        result: ExtractionResult[Any] = await extractor.extract(source, extraction_config=config)
        records = [item.model_dump(mode="json") for item in result]

        return json.dumps(
            {
                "records": records,
                "count": len(records),
                "report": {
                    "mode": result.report.mode.value,
                    "usage": {
                        "llm_calls": result.report.usage.llm_calls,
                        "input_tokens": result.report.usage.input_tokens,
                        "output_tokens": result.report.usage.output_tokens,
                        "total_tokens": result.report.usage.total_tokens,
                    },
                },
            },
            ensure_ascii=False,
            default=str,
        )

    # * Tool: suggest_schema

    @mcp.tool()
    async def suggest_schema(
        source: str,
        sheet: str | None = None,
        instructions: str | None = None,
        provider: str | None = None,
    ) -> str:
        """Suggest a Pydantic schema for an Excel/CSV file.

        Analyzes the spreadsheet structure and suggests field names, types,
        and descriptions that match the data.

        Args:
            source: File path or URL (local, s3://, az://, gs://).
            sheet: Target sheet name. Uses first sheet if omitted.
            instructions: Hints for schema generation (e.g. "focus on financial columns").
            provider: LLM provider string. Uses default if omitted.

        Returns:
            Python source code string defining a Pydantic model class.
        """
        _validate_source(source)
        extractor = _create_extractor(provider)
        model_cls = await extractor.suggest_schema(source, sheet=sheet, instructions=instructions)
        return render_schema_source(model_cls)

    # * Tool: generate_script

    @mcp.tool()
    async def generate_script(
        source: str,
        schema_json: str,
        provider: str | None = None,
        sheet: str | None = None,
    ) -> str:
        """Generate a reusable Python extraction script for an Excel/CSV file.

        Uses the code generation pipeline to produce a standalone script that
        can parse files with the same structure without further LLM calls.

        Args:
            source: File path or URL (local, s3://, az://, gs://).
            schema_json: JSON object defining field names and types.
            provider: LLM provider string. Uses default if omitted.
            sheet: Target sheet name. Uses first sheet if omitted.

        Returns:
            JSON string with the generated script code and explanation.
        """
        _validate_source(source)
        output_schema = build_model_from_schema_json(schema_json)
        extractor = _create_extractor(provider)

        config = ExtractionConfig(
            output_schema=output_schema,
            mode=ExtractionMode.CODEGEN,
            sheet=sheet,
        )

        script = await extractor.generate_script(source, extraction_config=config)
        return json.dumps(
            {"code": script.code, "explanation": script.explanation},
            ensure_ascii=False,
        )

    # * Tool: extract_batch

    @mcp.tool()
    async def extract_batch(
        sources: list[str],
        schema_json: str,
        provider: str | None = None,
        concurrency: int = 5,
    ) -> str:
        """Batch extract structured data from multiple Excel/CSV files.

        Processes files in parallel with a configurable concurrency limit.
        Individual file failures do not stop the batch.

        Args:
            sources: List of file paths or URLs.
            schema_json: JSON object defining field names and types.
            provider: LLM provider string. Uses default if omitted.
            concurrency: Max files processed simultaneously (default 5).

        Returns:
            JSON string with per-file results and aggregated usage.
        """
        for src in sources:
            _validate_source(src)

        output_schema = build_model_from_schema_json(schema_json)
        extractor = _create_extractor(provider)

        config = ExtractionConfig(output_schema=output_schema)
        batch_result: BatchResult[Any] = await extractor.extract_batch(
            sources, extraction_config=config, concurrency=concurrency
        )

        file_results = []
        for fr in batch_result.results:
            entry: dict[str, Any] = {
                "source": fr.source,
                "success": fr.success,
            }
            if fr.success and fr.records:
                entry["records"] = [r.model_dump(mode="json") for r in fr.records]
                entry["count"] = len(fr.records)
            if fr.error:
                entry["error"] = fr.error
            if fr.usage:
                entry["usage"] = {
                    "llm_calls": fr.usage.llm_calls,
                    "total_tokens": fr.usage.total_tokens,
                }
            file_results.append(entry)

        return json.dumps(
            {
                "results": file_results,
                "total_files": len(sources),
                "successful": sum(1 for fr in batch_result.results if fr.success),
                "failed": sum(1 for fr in batch_result.results if not fr.success),
            },
            ensure_ascii=False,
            default=str,
        )

    # * Tool: inspect_sheet

    @mcp.tool()
    async def inspect_sheet(
        source: str,
        sheet: str | None = None,
    ) -> str:
        """Inspect sheet structure without extraction.

        Returns metadata about the sheet: dimensions, column names,
        row count, merged regions, and a sample of detected header values.

        Args:
            source: File path or URL (local, s3://, az://, gs://).
            sheet: Target sheet name. Uses first sheet if omitted.

        Returns:
            JSON string with sheet structure information.
        """
        _validate_source(source)
        sheet_data = await _load_sheet(source, sheet)

        # * Extract header candidates from first few rows
        header_values: list[str] = []
        for cell in sheet_data.cells:
            if cell.row <= 3 and cell.value is not None:
                header_values.append(str(cell.value))

        # * Detect column names from row 1
        column_names: list[str] = []
        for cell in sorted(sheet_data.cells, key=lambda c: c.col):
            if cell.row == 1 and cell.value is not None:
                column_names.append(str(cell.value))

        return json.dumps(
            {
                "sheet_name": sheet_data.name,
                "dimensions": sheet_data.dimensions,
                "row_count": sheet_data.row_count,
                "col_count": sheet_data.col_count,
                "column_names": column_names,
                "merged_ranges": sheet_data.merged_ranges,
                "header_sample": header_values[:50],
            },
            ensure_ascii=False,
        )

    # * Tool: cache_list

    @mcp.tool()
    async def cache_list() -> str:
        """List cached codegen scripts.

        Returns metadata for all cached entries including signature,
        schema name, sheet name, and creation timestamp.

        Returns:
            JSON string with list of cache entries.
        """
        cache = ScriptCache()
        entries = cache.list_entries()
        return json.dumps(
            {
                "entries": [e.model_dump(mode="json") for e in entries],
                "count": len(entries),
            },
            ensure_ascii=False,
        )

    # * Tool: cache_clear

    @mcp.tool()
    async def cache_clear(signature: str | None = None) -> str:
        """Clear cached codegen scripts.

        If a signature is provided, removes only that specific entry.
        If no signature is provided, clears all cached scripts.

        Args:
            signature: Optional cache entry signature to remove.
                If omitted, all entries are cleared.

        Returns:
            JSON string with confirmation and count of removed entries.
        """
        cache = ScriptCache()
        if signature:
            removed = cache.remove(signature)
            return json.dumps(
                {
                    "action": "remove",
                    "signature": signature,
                    "removed": removed,
                },
            )
        else:
            count = cache.clear()
            return json.dumps(
                {
                    "action": "clear_all",
                    "removed_count": count,
                },
            )

    return mcp


def main() -> None:
    """Entry point for the xlstruct-mcp command."""
    server = create_mcp_server()
    server.run()


if __name__ == "__main__":
    main()
