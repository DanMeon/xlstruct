"""Tests for cell-address level provenance tracking."""

from pydantic import BaseModel

from xlstruct.config import ExtractionMode
from xlstruct.extraction.engine import ExtractionEngine, _build_provenance_schema
from xlstruct.schemas.report import ExtractionReport
from xlstruct.schemas.usage import TokenUsage

# * Test schema


class Product(BaseModel):
    name: str
    price: float
    stock: int


# * _build_provenance_schema tests


class TestBuildProvenanceSchema:
    def test_includes_source_rows_field(self):
        schema = _build_provenance_schema(Product)
        assert "source_rows" in schema.model_fields

    def test_includes_source_cells_field(self):
        schema = _build_provenance_schema(Product)
        assert "source_cells" in schema.model_fields

    def test_preserves_original_fields(self):
        schema = _build_provenance_schema(Product)
        assert "name" in schema.model_fields
        assert "price" in schema.model_fields
        assert "stock" in schema.model_fields

    def test_source_cells_type(self):
        """source_cells should accept dict[str, str]."""
        schema = _build_provenance_schema(Product)
        instance = schema(
            name="Apple",
            price=1.5,
            stock=100,
            source_rows=[2],
            source_cells={"name": "A2", "price": "B2", "stock": "C2"},
        )
        assert instance.source_cells == {"name": "A2", "price": "B2", "stock": "C2"}

    def test_schema_name(self):
        schema = _build_provenance_schema(Product)
        assert schema.__name__ == "ProductWithProvenance"


# * _split_provenance tests


class TestSplitProvenance:
    def test_extracts_source_rows(self):
        wrapper_schema = _build_provenance_schema(Product)
        items = [
            wrapper_schema(
                name="Apple",
                price=1.5,
                stock=100,
                source_rows=[2],
                source_cells={"name": "A2", "price": "B2", "stock": "C2"},
            ),
        ]
        result = ExtractionEngine._split_provenance(items, Product)
        assert len(result) == 1
        assert result[0]._source_rows == [2]  # type: ignore

    def test_extracts_source_cells(self):
        wrapper_schema = _build_provenance_schema(Product)
        items = [
            wrapper_schema(
                name="Apple",
                price=1.5,
                stock=100,
                source_rows=[2],
                source_cells={"name": "A2", "price": "B2", "stock": "C2"},
            ),
        ]
        result = ExtractionEngine._split_provenance(items, Product)
        assert result[0]._source_cells == {"name": "A2", "price": "B2", "stock": "C2"}  # type: ignore

    def test_original_schema_intact(self):
        """Returned records should be instances of the original schema without provenance fields."""
        wrapper_schema = _build_provenance_schema(Product)
        items = [
            wrapper_schema(
                name="Banana",
                price=0.75,
                stock=200,
                source_rows=[3],
                source_cells={"name": "A3", "price": "B3", "stock": "C3"},
            ),
        ]
        result = ExtractionEngine._split_provenance(items, Product)
        assert isinstance(result[0], Product)
        assert result[0].name == "Banana"
        # ^ Provenance fields should not be in model_dump
        assert "source_rows" not in result[0].model_dump()
        assert "source_cells" not in result[0].model_dump()

    def test_empty_source_cells_defaults(self):
        """Missing source_cells in data should default to empty dict."""
        wrapper_schema = _build_provenance_schema(Product)
        # ^ Simulate a record where LLM returned empty source_cells
        items = [
            wrapper_schema(
                name="Cherry",
                price=3.0,
                stock=50,
                source_rows=[4],
                source_cells={},
            ),
        ]
        result = ExtractionEngine._split_provenance(items, Product)
        assert result[0]._source_cells == {}  # type: ignore

    def test_multiple_records(self):
        wrapper_schema = _build_provenance_schema(Product)
        items = [
            wrapper_schema(
                name="Apple",
                price=1.5,
                stock=100,
                source_rows=[2],
                source_cells={"name": "A2", "price": "B2", "stock": "C2"},
            ),
            wrapper_schema(
                name="Banana",
                price=0.75,
                stock=200,
                source_rows=[3],
                source_cells={"name": "A3", "price": "B3", "stock": "C3"},
            ),
        ]
        result = ExtractionEngine._split_provenance(items, Product)
        assert len(result) == 2
        assert result[0]._source_cells == {"name": "A2", "price": "B2", "stock": "C2"}  # type: ignore
        assert result[1]._source_cells == {"name": "A3", "price": "B3", "stock": "C3"}  # type: ignore


# * ExtractionReport tests


class TestExtractionReportSourceCells:
    def test_source_cells_default_empty(self):
        report = ExtractionReport(mode=ExtractionMode.DIRECT, usage=TokenUsage())
        assert report.source_cells == []

    def test_source_cells_populated(self):
        cells = [
            {"name": "A2", "price": "B2", "stock": "C2"},
            {"name": "A3", "price": "B3", "stock": "C3"},
        ]
        report = ExtractionReport(
            mode=ExtractionMode.DIRECT,
            usage=TokenUsage(),
            source_cells=cells,
        )
        assert report.source_cells == cells
        assert len(report.source_cells) == 2

    def test_source_cells_none_not_allowed(self):
        """source_cells should be a list, not None (uses default_factory)."""
        report = ExtractionReport(mode=ExtractionMode.DIRECT, usage=TokenUsage())
        assert report.source_cells is not None

    def test_summary_includes_cell_provenance(self):
        cells = [{"name": "A2"}, {"name": "A3"}]
        report = ExtractionReport(
            mode=ExtractionMode.DIRECT,
            usage=TokenUsage(input_tokens=100, output_tokens=50, total_tokens=150),
            source_rows=[[2], [3]],
            source_cells=cells,
        )
        text = report.summary()
        assert "Cell provenance: 2 records mapped" in text

    def test_summary_no_cell_provenance_when_empty(self):
        report = ExtractionReport(
            mode=ExtractionMode.DIRECT,
            usage=TokenUsage(input_tokens=100, output_tokens=50, total_tokens=150),
        )
        text = report.summary()
        assert "Cell provenance" not in text

    def test_cell_address_format(self):
        """Cell addresses should be strings like 'A1', 'C14', not numeric pairs."""
        cells = [{"name": "A5", "amount": "C14", "date": "D5"}]
        report = ExtractionReport(
            mode=ExtractionMode.DIRECT,
            usage=TokenUsage(),
            source_cells=cells,
        )
        for cell_map in report.source_cells:
            for field_name, addr in cell_map.items():
                assert isinstance(addr, str)
                # ^ Validate cell address format: letter(s) + digit(s)
                assert addr[0].isalpha(), f"Cell address '{addr}' should start with a letter"
                assert addr[-1].isdigit(), f"Cell address '{addr}' should end with a digit"

    def test_source_rows_and_source_cells_coexist(self):
        """Both provenance types should work together."""
        report = ExtractionReport(
            mode=ExtractionMode.DIRECT,
            usage=TokenUsage(),
            source_rows=[[2], [3]],
            source_cells=[
                {"name": "A2", "price": "B2"},
                {"name": "A3", "price": "B3"},
            ],
        )
        assert len(report.source_rows) == 2
        assert len(report.source_cells) == 2


# * Prompt tests


class TestProvenancePrompt:
    def test_prompt_includes_cell_provenance_section(self):
        from xlstruct.prompts.extraction import build_extraction_prompt

        prompt = build_extraction_prompt("test data", track_provenance=True)
        assert "Cell Provenance" in prompt
        assert "source_cells" in prompt

    def test_prompt_no_cell_provenance_when_disabled(self):
        from xlstruct.prompts.extraction import build_extraction_prompt

        prompt = build_extraction_prompt("test data", track_provenance=False)
        assert "Cell Provenance" not in prompt
        assert "source_cells" not in prompt
