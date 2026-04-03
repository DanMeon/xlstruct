# pyright: reportUnusedFunction=false
"""XLStruct CLI — extract structured data from Excel files."""

import csv
import glob
import importlib
import json
import sys
from pathlib import Path as PathLibPath

import typer
from pydantic import BaseModel

from xlstruct.suggest import render_schema_source

app = typer.Typer(name="xlstruct", help="LLM-powered Excel parser")
cache_app = typer.Typer(name="cache", help="Manage codegen script cache")
app.add_typer(cache_app, name="cache")


# * Schema import helper


def import_schema(path: str) -> type[BaseModel]:
    """Import a Pydantic model from a Python dotted path.

    Format: "module.path:ClassName" (e.g. "myapp.models:Invoice")
    """
    if ":" not in path:
        raise typer.BadParameter(
            f"Invalid schema path: '{path}'. Expected format: 'module.path:ClassName'"
        )

    module_path, class_name = path.rsplit(":", 1)

    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as e:
        raise typer.BadParameter(f"Cannot import module '{module_path}': {e}") from e

    cls = getattr(module, class_name, None)
    if cls is None:
        raise typer.BadParameter(f"Class '{class_name}' not found in module '{module_path}'")

    if not (isinstance(cls, type) and issubclass(cls, BaseModel)):
        raise typer.BadParameter(f"'{class_name}' is not a Pydantic BaseModel subclass")

    return cls


# * Output formatting helpers


def _records_to_json(records: list[BaseModel]) -> str:
    """Serialize a list of Pydantic records to a JSON string."""
    return json.dumps([r.model_dump(mode="json") for r in records], indent=2, ensure_ascii=False)


def _records_to_csv(records: list[BaseModel]) -> str:
    """Serialize a list of Pydantic records to CSV string."""
    if not records:
        return ""
    from io import StringIO

    output = StringIO()
    fields = list(type(records[0]).model_fields.keys())
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for record in records:
        writer.writerow(record.model_dump(mode="json"))
    return output.getvalue()


def _format_records(records: list[BaseModel], fmt: str) -> str:
    """Format records as JSON or CSV."""
    if fmt == "csv":
        return _records_to_csv(records)
    return _records_to_json(records)


# * Schema source rendering (used by suggest command)


# * Commands


@app.command()
def suggest(
    source: str = typer.Argument(..., help="Excel file path or URL (s3://, az://, gs://)"),
    provider: str = typer.Option("openai/gpt-4o", "--provider", "-p"),
    sheet: str | None = typer.Option(None, "--sheet"),
    instructions: str | None = typer.Option(None, "--instructions", "-i"),
    output: str | None = typer.Option(None, "--output", "-o", help="Save schema to .py file"),
) -> None:
    """Analyze an Excel file and suggest a Pydantic schema."""
    from xlstruct.extractor import Extractor

    extractor = Extractor(provider=provider)
    model_cls = extractor.suggest_schema_sync(source, sheet=sheet, instructions=instructions)

    source_code = render_schema_source(model_cls)

    if output:
        PathLibPath(output).write_text(source_code, encoding="utf-8")
        typer.echo(f"Schema written to {output}")
    else:
        typer.echo(source_code)


@app.command()
def extract(
    source: str = typer.Argument(..., help="Excel/CSV file path or URL"),
    schema: str = typer.Option(..., "--schema", "-s", help="Pydantic model path (module:Class)"),
    provider: str = typer.Option("openai/gpt-4o", "--provider", "-p"),
    sheet: str | None = typer.Option(None, "--sheet"),
    mode: str = typer.Option("auto", "--mode", "-m", help="Extraction mode: auto/direct/codegen"),
    instructions: str | None = typer.Option(None, "--instructions", "-i"),
    output: str | None = typer.Option(None, "--output", "-o", help="Output file path"),
    fmt: str = typer.Option("json", "--format", "-f", help="Output format: json/csv"),
) -> None:
    """Extract structured data from an Excel/CSV file."""
    from xlstruct.config import ExtractionConfig, ExtractionMode
    from xlstruct.extractor import Extractor

    model_cls = import_schema(schema)

    extraction_mode = ExtractionMode(mode)
    extraction_config = ExtractionConfig(
        output_schema=model_cls,
        mode=extraction_mode,
        sheet=sheet,
        instructions=instructions,
    )

    extractor = Extractor(provider=provider)
    result: list[BaseModel] = list(
        extractor.extract_sync(source, extraction_config=extraction_config)
    )

    formatted = _format_records(result, fmt)

    if output:
        PathLibPath(output).write_text(formatted, encoding="utf-8")
        typer.echo(f"Extracted {len(result)} records -> {output}")
    else:
        typer.echo(formatted)


@app.command()
def batch(
    path_pattern: str = typer.Argument(..., help="Directory or glob pattern (e.g. 'data/*.xlsx')"),
    schema: str = typer.Option(..., "--schema", "-s", help="Pydantic model path (module:Class)"),
    provider: str = typer.Option("openai/gpt-4o", "--provider", "-p"),
    concurrency: int = typer.Option(5, "--concurrency", "-c", help="Max parallel files"),
    output: str | None = typer.Option(None, "--output", "-o", help="Output directory"),
) -> None:
    """Batch extract structured data from multiple Excel/CSV files."""
    from xlstruct.extractor import Extractor

    model_cls = import_schema(schema)

    # * Resolve file list from glob or directory
    source_path = PathLibPath(path_pattern)
    if source_path.is_dir():
        files = sorted(
            str(p)
            for p in source_path.iterdir()
            if p.suffix.lower() in (".xlsx", ".xlsm", ".xls", ".csv", ".xlsb", ".xltx", ".xltm")
        )
    else:
        files = sorted(glob.glob(path_pattern, recursive=True))

    if not files:
        typer.echo(f"No files found matching: {path_pattern}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Processing {len(files)} file(s) with concurrency={concurrency}...")

    extractor = Extractor(provider=provider)
    batch_result = extractor.extract_batch_sync(
        files,
        model_cls,
        concurrency=concurrency,
    )

    # * Write per-file results
    output_dir: PathLibPath | None = None
    if output:
        output_dir = PathLibPath(output)
        output_dir.mkdir(parents=True, exist_ok=True)

    for file_result in batch_result.results:
        file_json = _records_to_json(file_result.records)
        if output_dir:
            stem = PathLibPath(file_result.source).stem
            out_path = output_dir / f"{stem}.json"
            out_path.write_text(file_json, encoding="utf-8")
        else:
            typer.echo(f"\n--- {file_result.source} ---")
            if file_result.success:
                typer.echo(file_json)
            else:
                typer.echo(f"ERROR: {file_result.error}", err=True)

    # * Summary
    typer.echo(
        f"\nBatch complete: {batch_result.succeeded}/{batch_result.total} succeeded, "
        f"{batch_result.failed} failed"
    )


# * Cache subcommands


@cache_app.command("list")
def cache_list(
    fmt: str = typer.Option("table", "--format", "-f", help="Output format: table/json"),
) -> None:
    """List cached codegen scripts."""
    from xlstruct.codegen.cache import ScriptCache

    cache = ScriptCache()
    entries = cache.list_entries()

    if not entries:
        typer.echo("No cached entries.")
        return

    if fmt == "json":
        typer.echo(json.dumps([e.model_dump() for e in entries], indent=2, ensure_ascii=False))
        return

    # * Table format
    typer.echo(f"{'SIGNATURE':<20} {'SCHEMA':<20} {'SHEET':<15} {'CREATED':<25}")
    typer.echo("-" * 80)
    for entry in entries:
        sig = entry.signature[:18] + ".." if len(entry.signature) > 20 else entry.signature
        typer.echo(
            f"{sig:<20} {entry.schema_name:<20} {entry.sheet_name:<15} {entry.created_at:<25}"
        )

    typer.echo(f"\nTotal: {len(entries)} cached script(s)")


@cache_app.command("clear")
def cache_clear(
    confirm: bool = typer.Option(False, "--confirm", help="Skip confirmation prompt"),
) -> None:
    """Clear all cached codegen scripts."""
    from xlstruct.codegen.cache import ScriptCache

    cache = ScriptCache()

    if not confirm:
        entries = cache.list_entries()
        if not entries:
            typer.echo("No cached entries to clear.")
            return
        typer.confirm(f"Remove all {len(entries)} cached script(s)?", abort=True)

    removed = cache.clear()
    typer.echo(f"Cleared {removed} cached script(s).")


@cache_app.command("remove")
def cache_remove(
    signature: str = typer.Argument(..., help="Cache entry signature to remove"),
) -> None:
    """Remove a specific cached codegen script."""
    from xlstruct.codegen.cache import ScriptCache

    cache = ScriptCache()
    removed = cache.remove(signature)

    if removed:
        typer.echo(f"Removed cached script: {signature}")
    else:
        typer.echo(f"No cached entry found for signature: {signature}", err=True)
        raise typer.Exit(code=1)


def _cli_entry() -> None:
    """Entry point that adds cwd to sys.path for schema imports."""
    cwd = str(PathLibPath.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)
    app()
