"""Schema models for suggest_schema() LLM response."""

from typing import Literal

from pydantic import BaseModel, Field


class FieldDef(BaseModel):
    """Single field definition returned by the LLM."""

    name: str = Field(description="snake_case field name")
    type: Literal["str", "int", "float", "bool", "date", "datetime"] = Field(
        description="Python type name"
    )
    nullable: bool = Field(description="Whether the field can be None")
    description: str = Field(description="Description mentioning the original Excel column")


class SuggestedFields(BaseModel):
    """LLM response containing field definitions for a suggested schema."""

    model_name: str = Field(description="PascalCase model class name")
    fields: list[FieldDef] = Field(description="List of field definitions")
