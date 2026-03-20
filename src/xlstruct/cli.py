"""XLStruct CLI — schema suggestion from Excel files."""

from pathlib import Path as PathLibPath

import typer
from pydantic import BaseModel

app = typer.Typer(name="xlstruct", help="LLM-powered Excel parser")


def _render_schema_source(model_cls: "type[BaseModel]") -> str:
    """Convert a dynamically created Pydantic model to Python source code."""
    # ^ Collect type names for imports
    imports: set[str] = set()
    lines: list[str] = []

    for name, field_info in model_cls.model_fields.items():
        annotation = field_info.annotation
        type_str = _annotation_to_str(annotation, imports) if annotation else "str"
        desc = field_info.description or ""
        lines.append(f'    {name}: {type_str} = Field(description="{desc}")')

    # * Build import block
    import_lines = ["from pydantic import BaseModel, Field"]
    if imports:
        import_lines.insert(0, f"from datetime import {', '.join(sorted(imports))}")

    header = "\n".join(import_lines)
    body = "\n".join(lines)

    return f"{header}\n\n\nclass {model_cls.__name__}(BaseModel):\n{body}\n"


def _annotation_to_str(annotation: type, imports: set[str]) -> str:
    """Convert a type annotation to its string representation."""
    import types
    from typing import get_args, get_origin

    origin = get_origin(annotation)

    # ^ Union type (X | None)
    if origin is types.UnionType:
        args = get_args(annotation)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1 and len(args) == 2:
            inner = _annotation_to_str(non_none[0], imports)
            return f"{inner} | None"

    # ^ Simple types
    type_name_map: dict[type, str] = {
        str: "str",
        int: "int",
        float: "float",
        bool: "bool",
    }
    if annotation in type_name_map:
        return type_name_map[annotation]

    # ^ datetime types
    import datetime

    if annotation is datetime.date:
        imports.add("date")
        return "date"
    if annotation is datetime.datetime:
        imports.add("datetime")
        return "datetime"

    return annotation.__name__ if hasattr(annotation, "__name__") else str(annotation)


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

    source_code = _render_schema_source(model_cls)

    if output:
        PathLibPath(output).write_text(source_code, encoding="utf-8")
        typer.echo(f"Schema written to {output}")
    else:
        typer.echo(source_code)
