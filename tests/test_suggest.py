"""Tests for suggest_schema Python API and render_schema_source utility."""

import datetime
from unittest.mock import AsyncMock, patch

from pydantic import BaseModel, Field, create_model

from xlstruct.extractor import Extractor
from xlstruct.suggest import render_schema_source

# * render_schema_source tests


class TestRenderSchemaSource:
    def test_simple_model(self):
        Model = create_model(
            "Invoice",
            amount=(float, Field(description="Total amount")),
            name=(str, Field(description="Customer name")),
        )
        source = render_schema_source(Model)
        assert "class Invoice(BaseModel):" in source
        assert "amount: float" in source
        assert "name: str" in source
        assert "from pydantic import BaseModel, Field" in source

    def test_nullable_field(self):
        Model = create_model(
            "Row",
            value=(float | None, Field(description="Optional value")),
        )
        source = render_schema_source(Model)
        assert "float | None" in source

    def test_date_fields(self):
        Model = create_model(
            "Event",
            start=(datetime.date, Field(description="Start date")),
            created=(datetime.datetime, Field(description="Created at")),
        )
        source = render_schema_source(Model)
        assert "from datetime import date, datetime" in source
        assert "start: date" in source
        assert "created: datetime" in source

    def test_output_is_valid_python(self):
        """Rendered source code should be valid, importable Python."""
        Model = create_model(
            "Product",
            name=(str, Field(description="Product name")),
            price=(float, Field(description="Unit price")),
            quantity=(int | None, Field(description="Quantity")),
        )
        source = render_schema_source(Model)
        # ^ compile() validates syntax without executing
        compile(source, "<test>", "exec")

    def test_all_supported_types(self):
        """All type mappings (str, int, float, bool, date, datetime) render correctly."""
        Model = create_model(
            "AllTypes",
            s=(str, Field(description="string")),
            i=(int, Field(description="integer")),
            f=(float, Field(description="float")),
            b=(bool, Field(description="boolean")),
            d=(datetime.date, Field(description="date")),
            dt=(datetime.datetime, Field(description="datetime")),
        )
        source = render_schema_source(Model)
        assert "s: str" in source
        assert "i: int" in source
        assert "f: float" in source
        assert "b: bool" in source
        assert "d: date" in source
        assert "dt: datetime" in source
        assert "from datetime import date, datetime" in source

    def test_model_with_no_description(self):
        """Fields without descriptions render with empty description string."""
        Model = create_model("Bare", x=(int, ...))
        source = render_schema_source(Model)
        assert "x: int" in source
        assert 'description=""' in source


# * Extractor.suggest_schema_source tests


def _make_mock_model() -> type[BaseModel]:
    return create_model(
        "Order",
        order_id=(int, Field(description="Order ID")),
        customer=(str, Field(description="Customer name")),
        total=(float | None, Field(description="Total amount")),
    )


class TestSuggestSchemaSource:
    async def test_suggest_schema_source_returns_source_code(self):
        """suggest_schema_source() returns valid Python source code string."""
        mock_model = _make_mock_model()

        extractor = Extractor.__new__(Extractor)
        with patch.object(extractor, "suggest_schema", new_callable=AsyncMock) as mock_suggest:
            mock_suggest.return_value = mock_model
            result = await extractor.suggest_schema_source("test.xlsx")

        assert isinstance(result, str)
        assert "class Order(BaseModel):" in result
        assert "order_id: int" in result
        assert "customer: str" in result
        assert "float | None" in result
        assert "from pydantic import BaseModel, Field" in result
        # ^ Valid Python syntax
        compile(result, "<test>", "exec")

    async def test_suggest_schema_source_passes_kwargs(self):
        """suggest_schema_source() forwards sheet and instructions to suggest_schema()."""
        mock_model = _make_mock_model()

        extractor = Extractor.__new__(Extractor)
        with patch.object(extractor, "suggest_schema", new_callable=AsyncMock) as mock_suggest:
            mock_suggest.return_value = mock_model
            await extractor.suggest_schema_source(
                "test.xlsx", sheet="Sheet2", instructions="focus on prices"
            )

        mock_suggest.assert_called_once_with(
            "test.xlsx", sheet="Sheet2", instructions="focus on prices"
        )

    async def test_suggest_schema_source_passes_storage_options(self):
        """suggest_schema_source() forwards storage_options to suggest_schema()."""
        mock_model = _make_mock_model()

        extractor = Extractor.__new__(Extractor)
        with patch.object(extractor, "suggest_schema", new_callable=AsyncMock) as mock_suggest:
            mock_suggest.return_value = mock_model
            await extractor.suggest_schema_source("s3://bucket/file.xlsx", key="abc", secret="xyz")

        mock_suggest.assert_called_once_with(
            "s3://bucket/file.xlsx", sheet=None, instructions=None, key="abc", secret="xyz"
        )


class TestSuggestSchemaSourceSync:
    def test_suggest_schema_source_sync_returns_string(self):
        """suggest_schema_source_sync() returns the same source code string."""
        mock_model = _make_mock_model()

        extractor = Extractor.__new__(Extractor)
        with patch.object(extractor, "suggest_schema", new_callable=AsyncMock) as mock_suggest:
            mock_suggest.return_value = mock_model
            result = extractor.suggest_schema_source_sync("test.xlsx")

        assert isinstance(result, str)
        assert "class Order(BaseModel):" in result

    def test_suggest_schema_source_sync_with_options(self):
        """suggest_schema_source_sync() forwards keyword arguments."""
        mock_model = _make_mock_model()

        extractor = Extractor.__new__(Extractor)
        with patch.object(extractor, "suggest_schema", new_callable=AsyncMock) as mock_suggest:
            mock_suggest.return_value = mock_model
            extractor.suggest_schema_source_sync(
                "test.xlsx", sheet="Data", instructions="keep dates"
            )

        mock_suggest.assert_called_once_with("test.xlsx", sheet="Data", instructions="keep dates")
