"""XLStruct CLI — schema suggestion from Excel files."""

from pathlib import Path as PathLibPath

import typer

from xlstruct.suggest import render_schema_source

app = typer.Typer(name="xlstruct", help="LLM-powered Excel parser")


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
