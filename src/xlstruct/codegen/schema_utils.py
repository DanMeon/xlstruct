"""Utilities for extracting Pydantic schema source code."""

import inspect
from typing import get_args, get_origin

from pydantic import BaseModel


def get_schema_source(schema_cls: type[BaseModel]) -> str:
    """Extract source code of schema class and its nested model dependencies.

    Traverses model_fields to find referenced BaseModel subclasses,
    then returns source code in dependency order (dependencies first).

    Falls back to model_json_schema() if inspect.getsource() fails.
    """
    # * Collect all referenced model classes in dependency order
    visited: set[type] = set()
    ordered: list[type] = []
    _collect_models(schema_cls, visited, ordered)

    # * Extract source for each class
    sources: list[str] = []
    for cls in ordered:
        try:
            source = inspect.getsource(cls)
            sources.append(source)
        except (OSError, TypeError):
            # ^ inspect.getsource() fails for dynamically created classes
            schema_json = cls.model_json_schema()  # type: ignore
            sources.append(f"# Schema for {cls.__name__} (source unavailable):\n# {schema_json}\n")

    return "\n\n".join(sources)


def _collect_models(
    cls: type[BaseModel],
    visited: set[type],
    ordered: list[type],
) -> None:
    """Recursively collect BaseModel subclasses in dependency order."""
    if cls in visited:
        return
    visited.add(cls)

    # ^ Traverse fields to find nested models
    for field_info in cls.model_fields.values():
        annotation = field_info.annotation
        if annotation is None:
            continue
        for dep in _extract_model_types(annotation):
            _collect_models(dep, visited, ordered)

    ordered.append(cls)


def _extract_model_types(annotation: type | object) -> list[type[BaseModel]]:
    """Extract BaseModel subclasses from a type annotation.

    Handles: SubModel, list[SubModel], dict[str, SubModel],
    SubModel | None, etc.
    """
    result: list[type[BaseModel]] = []

    # ^ Direct BaseModel subclass
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        result.append(annotation)
        return result

    # ^ Generic types: list[X], dict[K, V], X | None, etc.
    origin = get_origin(annotation)
    if origin is not None:
        for arg in get_args(annotation):
            if isinstance(arg, type) and issubclass(arg, BaseModel):
                result.append(arg)
            elif get_origin(arg) is not None:
                # ^ Nested generics
                result.extend(_extract_model_types(arg))  # type: ignore

    return result
