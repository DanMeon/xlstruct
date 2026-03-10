"""XLStruct CLI — thin wrapper over Extractor.extract_sync()."""

import importlib
import json
import sys
from pathlib import Path as PathLibPath
from typing import Any

import typer

app = typer.Typer(name="xlstruct", help="LLM-powered Excel parser")


def _resolve_schema(schema_str: str) -> type:
    """Resolve 'module.path:ClassName' string to actual class.

    Adds current directory to sys.path to support local modules.
    """
    if ":" not in schema_str:
        typer.echo(f"Error: schema must be 'module:ClassName', got '{schema_str}'", err=True)
        raise typer.Exit(1)

    module_path, class_name = schema_str.rsplit(":", 1)

    # ^ Ensure current dir is importable
    cwd = str(PathLibPath.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        typer.echo(f"Error: cannot import module '{module_path}': {e}", err=True)
        raise typer.Exit(1)

    cls: type | None = getattr(module, class_name, None)
    if cls is None:
        typer.echo(f"Error: class '{class_name}' not found in '{module_path}'", err=True)
        raise typer.Exit(1)

    return cls


@app.command()
def extract(
    source: str = typer.Argument(..., help="Excel file path or URL (s3://, az://, gs://)"),
    schema: str = typer.Option(..., "--schema", "-s", help="Pydantic model as 'module:Class'"),
    provider: str = typer.Option("openai/gpt-4o", "--provider", "-p"),
    mode: str = typer.Option("auto", "--mode", "-m", help="Extraction mode: auto, direct, codegen"),
    sheet: str | None = typer.Option(None, "--sheet"),
    instructions: str | None = typer.Option(None, "--instructions", "-i"),
    output: str | None = typer.Option(None, "--output", "-o", help="Output JSON file path"),
    temperature: float = typer.Option(0.0, "--temperature"),
    max_retries: int = typer.Option(3, "--max-retries"),
) -> None:
    """Extract structured data from an Excel file using LLM."""
    from xlstruct.config import ExtractionConfig, ExtractionMode  # ^ Lazy import
    from xlstruct.extractor import Extractor

    schema_cls = _resolve_schema(schema)
    extractor = Extractor(provider=provider, temperature=temperature, max_retries=max_retries)

    extraction_config = ExtractionConfig(
        mode=ExtractionMode(mode),
        output_schema=schema_cls,
        sheet=sheet,
        instructions=instructions,
    )

    # ^ CLI runs in sync context
    results: list[Any] = extractor.extract_sync(
        source, extraction_config=extraction_config
    )

    output_json = json.dumps(
        [r.model_dump() for r in results],
        ensure_ascii=False,
        indent=2,
        default=str,
    )

    if output:
        PathLibPath(output).write_text(output_json, encoding="utf-8")
        typer.echo(f"Results written to {output} ({len(results)} records)")
    else:
        typer.echo(output_json)
