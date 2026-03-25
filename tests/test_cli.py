"""Tests for CLI module."""

import json
from unittest.mock import MagicMock, patch

from pydantic import BaseModel, Field, create_model
from typer.testing import CliRunner

from xlstruct.cli import (
    _format_records,
    app,
    import_schema,
)
from xlstruct.suggest import render_schema_source

runner = CliRunner()


# * Test fixtures


class _MockInvoice(BaseModel):
    item: str = Field(description="Item name")
    amount: float = Field(description="Total amount")


class _NotAModel:
    pass


# * Schema source rendering


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
        import datetime

        Model = create_model(
            "Event",
            start=(datetime.date, Field(description="Start date")),
            created=(datetime.datetime, Field(description="Created at")),
        )
        source = render_schema_source(Model)
        assert "from datetime import date, datetime" in source
        assert "start: date" in source
        assert "created: datetime" in source


# * Schema import helper


class TestImportSchema:
    def test_valid_import(self):
        cls = import_schema("pydantic:BaseModel")
        assert cls is BaseModel

    def test_missing_colon(self):
        from typer import BadParameter

        try:
            import_schema("pydantic.BaseModel")
            assert False, "Should have raised BadParameter"
        except BadParameter as e:
            assert "Expected format" in str(e)

    def test_module_not_found(self):
        from typer import BadParameter

        try:
            import_schema("nonexistent_module_xyz:Foo")
            assert False, "Should have raised BadParameter"
        except BadParameter as e:
            assert "Cannot import module" in str(e)

    def test_class_not_found(self):
        from typer import BadParameter

        try:
            import_schema("pydantic:NonExistentClass")
            assert False, "Should have raised BadParameter"
        except BadParameter as e:
            assert "not found" in str(e)

    def test_not_a_base_model(self):
        from typer import BadParameter

        try:
            import_schema("json:JSONEncoder")
            assert False, "Should have raised BadParameter"
        except BadParameter as e:
            assert "not a Pydantic BaseModel" in str(e)


# * Output formatting


class TestFormatRecords:
    def test_json_format(self):
        records = [
            _MockInvoice(item="Widget", amount=9.99),
            _MockInvoice(item="Gadget", amount=19.99),
        ]
        result = _format_records(records, "json")
        parsed = json.loads(result)
        assert len(parsed) == 2
        assert parsed[0]["item"] == "Widget"
        assert parsed[1]["amount"] == 19.99

    def test_csv_format(self):
        records = [
            _MockInvoice(item="Widget", amount=9.99),
        ]
        result = _format_records(records, "csv")
        assert "item,amount" in result
        assert "Widget,9.99" in result

    def test_csv_empty(self):
        result = _format_records([], "csv")
        assert result == ""

    def test_json_empty(self):
        result = _format_records([], "json")
        assert json.loads(result) == []


# * Suggest command


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
            result = runner.invoke(app, ["suggest", "test.xlsx", "--provider", "openai/gpt-4o"])

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
            result = runner.invoke(app, ["suggest", "test.xlsx", "--output", str(output_path)])

        assert result.exit_code == 0, result.output
        content = output_path.read_text()
        assert "class Row(BaseModel):" in content


# * Extract command


class TestExtractCommand:
    def _make_mock_extractor(self, records):
        """Build a mock Extractor that returns given records from extract_sync."""
        mock = MagicMock()
        mock.extract_sync.return_value = records
        return mock

    def test_extract_json_stdout(self):
        """extract prints JSON to stdout by default."""
        records = [_MockInvoice(item="Widget", amount=9.99)]
        mock_ext = self._make_mock_extractor(records)

        with patch("xlstruct.cli.import_schema", return_value=_MockInvoice):
            with patch("xlstruct.extractor.Extractor", return_value=mock_ext):
                result = runner.invoke(
                    app,
                    ["extract", "test.xlsx", "--schema", "m:Invoice"],
                )

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert len(parsed) == 1
        assert parsed[0]["item"] == "Widget"

    def test_extract_csv_stdout(self):
        """extract --format csv prints CSV to stdout."""
        records = [_MockInvoice(item="Widget", amount=9.99)]
        mock_ext = self._make_mock_extractor(records)

        with patch("xlstruct.cli.import_schema", return_value=_MockInvoice):
            with patch("xlstruct.extractor.Extractor", return_value=mock_ext):
                result = runner.invoke(
                    app,
                    ["extract", "test.xlsx", "-s", "m:Invoice", "--format", "csv"],
                )

        assert result.exit_code == 0, result.output
        assert "item,amount" in result.output

    def test_extract_output_file(self, tmp_path):
        """extract --output writes to file."""
        records = [_MockInvoice(item="Gadget", amount=19.99)]
        mock_ext = self._make_mock_extractor(records)
        out = tmp_path / "out.json"

        with patch("xlstruct.cli.import_schema", return_value=_MockInvoice):
            with patch("xlstruct.extractor.Extractor", return_value=mock_ext):
                result = runner.invoke(
                    app,
                    ["extract", "test.xlsx", "-s", "m:Invoice", "-o", str(out)],
                )

        assert result.exit_code == 0, result.output
        assert "1 records" in result.output
        parsed = json.loads(out.read_text())
        assert parsed[0]["item"] == "Gadget"

    def test_extract_with_mode_and_instructions(self):
        """extract passes mode and instructions to ExtractionConfig."""
        records = [_MockInvoice(item="X", amount=1.0)]
        mock_ext = self._make_mock_extractor(records)

        with patch("xlstruct.cli.import_schema", return_value=_MockInvoice):
            with patch("xlstruct.extractor.Extractor", return_value=mock_ext):
                result = runner.invoke(
                    app,
                    [
                        "extract",
                        "test.xlsx",
                        "-s",
                        "m:Invoice",
                        "--mode",
                        "direct",
                        "-i",
                        "Focus on totals",
                    ],
                )

        assert result.exit_code == 0, result.output
        # ^ Verify ExtractionConfig was passed with correct mode
        call_kwargs = mock_ext.extract_sync.call_args
        config = call_kwargs.kwargs.get("extraction_config") or call_kwargs[1].get(
            "extraction_config"
        )
        assert config is not None
        assert config.mode.value == "direct"
        assert config.instructions == "Focus on totals"


# * Batch command


class TestBatchCommand:
    def test_batch_with_directory(self, tmp_path):
        """batch command processes files in a directory."""
        # ^ Create fake Excel files (just need the names to exist)
        (tmp_path / "a.xlsx").touch()
        (tmp_path / "b.xlsx").touch()
        (tmp_path / "readme.txt").touch()  # ^ Should be skipped

        mock_ext = MagicMock()
        mock_batch = MagicMock()
        mock_batch.results = [
            MagicMock(
                source=str(tmp_path / "a.xlsx"),
                success=True,
                records=[_MockInvoice(item="A", amount=1.0)],
            ),
            MagicMock(
                source=str(tmp_path / "b.xlsx"),
                success=True,
                records=[_MockInvoice(item="B", amount=2.0)],
            ),
        ]
        mock_batch.succeeded = 2
        mock_batch.failed = 0
        mock_batch.total = 2
        mock_ext.extract_batch_sync.return_value = mock_batch

        with patch("xlstruct.cli.import_schema", return_value=_MockInvoice):
            with patch("xlstruct.extractor.Extractor", return_value=mock_ext):
                result = runner.invoke(
                    app,
                    ["batch", str(tmp_path), "-s", "m:Invoice"],
                )

        assert result.exit_code == 0, result.output
        assert "2/2 succeeded" in result.output
        assert "0 failed" in result.output
        # ^ Verify only .xlsx files were passed (not readme.txt)
        call_args = mock_ext.extract_batch_sync.call_args
        sources = call_args[0][0]
        assert len(sources) == 2
        assert all(s.endswith(".xlsx") for s in sources)

    def test_batch_output_directory(self, tmp_path):
        """batch --output writes per-file JSON to output dir."""
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "report.xlsx").touch()

        output_dir = tmp_path / "results"

        mock_ext = MagicMock()
        mock_batch = MagicMock()
        mock_batch.results = [
            MagicMock(
                source=str(tmp_path / "data" / "report.xlsx"),
                success=True,
                records=[_MockInvoice(item="R", amount=5.0)],
            ),
        ]
        mock_batch.succeeded = 1
        mock_batch.failed = 0
        mock_batch.total = 1
        mock_ext.extract_batch_sync.return_value = mock_batch

        with patch("xlstruct.cli.import_schema", return_value=_MockInvoice):
            with patch("xlstruct.extractor.Extractor", return_value=mock_ext):
                result = runner.invoke(
                    app,
                    [
                        "batch",
                        str(tmp_path / "data"),
                        "-s",
                        "m:Invoice",
                        "-o",
                        str(output_dir),
                    ],
                )

        assert result.exit_code == 0, result.output
        out_file = output_dir / "report.json"
        assert out_file.exists()
        parsed = json.loads(out_file.read_text())
        assert parsed[0]["item"] == "R"

    def test_batch_no_files(self, tmp_path):
        """batch exits with code 1 when no files match."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        with patch("xlstruct.cli.import_schema", return_value=_MockInvoice):
            result = runner.invoke(
                app,
                ["batch", str(empty_dir), "-s", "m:Invoice"],
            )

        assert result.exit_code == 1


# * Cache commands


class TestCacheListCommand:
    def test_cache_list_empty(self):
        """cache list prints message when no entries."""
        mock_cache = MagicMock()
        mock_cache.list_entries.return_value = []

        with patch("xlstruct.codegen.cache.ScriptCache", return_value=mock_cache):
            result = runner.invoke(app, ["cache", "list"])

        assert result.exit_code == 0
        assert "No cached entries" in result.output

    def test_cache_list_table(self):
        """cache list shows table format by default."""
        mock_entry = MagicMock()
        mock_entry.signature = "abc123"
        mock_entry.schema_name = "Invoice"
        mock_entry.sheet_name = "Sheet1"
        mock_entry.created_at = "2025-01-01T00:00:00"

        mock_cache = MagicMock()
        mock_cache.list_entries.return_value = [mock_entry]

        with patch("xlstruct.codegen.cache.ScriptCache", return_value=mock_cache):
            result = runner.invoke(app, ["cache", "list"])

        assert result.exit_code == 0
        assert "abc123" in result.output
        assert "Invoice" in result.output
        assert "Sheet1" in result.output
        assert "Total: 1" in result.output

    def test_cache_list_json(self):
        """cache list --format json outputs JSON."""
        mock_entry = MagicMock()
        mock_entry.model_dump.return_value = {
            "signature": "abc123",
            "schema_name": "Invoice",
        }

        mock_cache = MagicMock()
        mock_cache.list_entries.return_value = [mock_entry]

        with patch("xlstruct.codegen.cache.ScriptCache", return_value=mock_cache):
            result = runner.invoke(app, ["cache", "list", "--format", "json"])

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert len(parsed) == 1
        assert parsed[0]["signature"] == "abc123"


class TestCacheClearCommand:
    def test_cache_clear_with_confirm(self):
        """cache clear --confirm skips prompt."""
        mock_cache = MagicMock()
        mock_cache.clear.return_value = 3

        with patch("xlstruct.codegen.cache.ScriptCache", return_value=mock_cache):
            result = runner.invoke(app, ["cache", "clear", "--confirm"])

        assert result.exit_code == 0
        assert "Cleared 3" in result.output
        mock_cache.clear.assert_called_once()

    def test_cache_clear_empty(self):
        """cache clear with no entries prints message."""
        mock_cache = MagicMock()
        mock_cache.list_entries.return_value = []

        with patch("xlstruct.codegen.cache.ScriptCache", return_value=mock_cache):
            result = runner.invoke(app, ["cache", "clear"])

        assert result.exit_code == 0
        assert "No cached entries to clear" in result.output


class TestCacheRemoveCommand:
    def test_cache_remove_success(self):
        """cache remove deletes entry by signature."""
        mock_cache = MagicMock()
        mock_cache.remove.return_value = True

        with patch("xlstruct.codegen.cache.ScriptCache", return_value=mock_cache):
            result = runner.invoke(app, ["cache", "remove", "abc123"])

        assert result.exit_code == 0
        assert "Removed cached script: abc123" in result.output
        mock_cache.remove.assert_called_once_with("abc123")

    def test_cache_remove_not_found(self):
        """cache remove exits 1 when signature not found."""
        mock_cache = MagicMock()
        mock_cache.remove.return_value = False

        with patch("xlstruct.codegen.cache.ScriptCache", return_value=mock_cache):
            result = runner.invoke(app, ["cache", "remove", "nonexistent"])

        assert result.exit_code == 1
        assert "No cached entry found" in result.output
