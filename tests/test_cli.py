"""Tests for CLI module."""

from unittest.mock import patch

from pydantic import Field, create_model
from typer.testing import CliRunner

from xlstruct.cli import _render_schema_source, app

runner = CliRunner()


class TestRenderSchemaSource:
    def test_simple_model(self):
        Model = create_model(
            "Invoice",
            amount=(float, Field(description="Total amount")),
            name=(str, Field(description="Customer name")),
        )
        source = _render_schema_source(Model)
        assert "class Invoice(BaseModel):" in source
        assert "amount: float" in source
        assert "name: str" in source
        assert "from pydantic import BaseModel, Field" in source

    def test_nullable_field(self):
        Model = create_model(
            "Row",
            value=(float | None, Field(description="Optional value")),
        )
        source = _render_schema_source(Model)
        assert "float | None" in source

    def test_date_fields(self):
        import datetime

        Model = create_model(
            "Event",
            start=(datetime.date, Field(description="Start date")),
            created=(datetime.datetime, Field(description="Created at")),
        )
        source = _render_schema_source(Model)
        assert "from datetime import date, datetime" in source
        assert "start: date" in source
        assert "created: datetime" in source


class TestSuggestCommand:
    def test_suggest_stdout(self):
        """suggest command prints schema source to stdout."""
        mock_model = create_model(
            "Product",
            name=(str, Field(description="Product name")),
            price=(float, Field(description="Unit price")),
        )

        mock_extractor = patch(
            "xlstruct.extractor.Extractor",
            return_value=type("E", (), {"suggest_schema_sync": lambda *a, **kw: mock_model})(),
        )
        with mock_extractor:
            result = runner.invoke(app, ["test.xlsx", "--provider", "openai/gpt-4o"])

        assert result.exit_code == 0, result.output
        assert "class Product(BaseModel):" in result.output
        assert "name: str" in result.output
        assert "price: float" in result.output

    def test_suggest_output_to_file(self, tmp_path):
        """suggest --output writes schema to .py file."""
        mock_model = create_model(
            "Row",
            x=(int, Field(description="X value")),
        )
        output_path = tmp_path / "schema.py"

        mock_extractor = patch(
            "xlstruct.extractor.Extractor",
            return_value=type("E", (), {"suggest_schema_sync": lambda *a, **kw: mock_model})(),
        )
        with mock_extractor:
            result = runner.invoke(app, ["test.xlsx", "--output", str(output_path)])

        assert result.exit_code == 0, result.output
        content = output_path.read_text()
        assert "class Row(BaseModel):" in content
