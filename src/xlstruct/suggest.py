"""Utilities for converting dynamically created Pydantic models to source code."""

import datetime
import types
from typing import get_args, get_origin

from pydantic import BaseModel


def render_schema_source(model_cls: type[BaseModel]) -> str:
    """Convert a dynamically created Pydantic model to Python source code.

    Takes a Pydantic model class (typically from ``Extractor.suggest_schema()``)
    and renders it as valid, importable Python source code with proper imports.

    Args:
        model_cls: A Pydantic model class with typed fields.

    Returns:
        Python source code string defining the model class.
    """
    # * Collect type names for imports
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
    if annotation is datetime.date:
        imports.add("date")
        return "date"
    if annotation is datetime.datetime:
        imports.add("datetime")
        return "datetime"

    return annotation.__name__ if hasattr(annotation, "__name__") else str(annotation)
