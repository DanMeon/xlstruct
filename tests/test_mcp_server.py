"""Tests for the MCP server module."""

import json
from pathlib import Path as PathLibPath
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xlstruct.mcp_server import (
    TYPE_MAP,
    _validate_source,
    build_model_from_schema_json,
    create_mcp_server,
)

# * build_model_from_schema_json tests


class TestBuildModelFromSchemaJson:
    """Tests for dynamic Pydantic model creation from JSON schema."""

    def test_simple_string_types(self):
        schema_json = '{"name": "str", "age": "int", "score": "float"}'
        model = build_model_from_schema_json(schema_json)

        assert model.__name__ == "DynamicSchema"
        fields = model.model_fields
        assert "name" in fields
        assert "age" in fields
        assert "score" in fields

    def test_detailed_field_definitions(self):
        schema_json = json.dumps(
            {
                "name": {"type": "str", "description": "Full name"},
                "amount": {"type": "float", "nullable": True},
            }
        )
        model = build_model_from_schema_json(schema_json)

        fields = model.model_fields
        assert "name" in fields
        assert "amount" in fields
        assert fields["name"].description == "Full name"

    def test_nullable_field(self):
        schema_json = '{"value": {"type": "int", "nullable": true}}'
        model = build_model_from_schema_json(schema_json)

        # ^ Nullable field should accept None
        instance = model(value=None)
        assert instance.value is None

        instance2 = model(value=42)
        assert instance2.value == 42

    def test_all_type_aliases(self):
        for type_str in TYPE_MAP:
            schema_json = json.dumps({"field": type_str})
            model = build_model_from_schema_json(schema_json)
            assert "field" in model.model_fields

    def test_case_insensitive_types(self):
        schema_json = '{"val": "STRING"}'
        model = build_model_from_schema_json(schema_json)
        assert "val" in model.model_fields

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            build_model_from_schema_json("{bad json")

    def test_empty_object_raises(self):
        with pytest.raises(ValueError, match="non-empty JSON object"):
            build_model_from_schema_json("{}")

    def test_non_object_raises(self):
        with pytest.raises(ValueError, match="non-empty JSON object"):
            build_model_from_schema_json("[]")

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown type 'vector'"):
            build_model_from_schema_json('{"emb": "vector"}')

    def test_model_instantiation(self):
        schema_json = '{"name": "str", "amount": "float"}'
        model = build_model_from_schema_json(schema_json)
        instance = model(name="Test", amount=99.5)
        assert instance.name == "Test"
        assert instance.amount == 99.5

    def test_model_serialization(self):
        schema_json = '{"x": "int", "y": "str"}'
        model = build_model_from_schema_json(schema_json)
        instance = model(x=1, y="hello")
        dumped = instance.model_dump()
        assert dumped == {"x": 1, "y": "hello"}

    # * list / nested object / enum support

    def test_list_field(self):
        schema_json = '{"tags": {"type": "list", "items": "str"}}'
        model = build_model_from_schema_json(schema_json)
        instance = model(tags=["a", "b"])
        assert instance.tags == ["a", "b"]

    def test_nested_object_field(self):
        schema_json = (
            '{"address": {"type": "object", "properties": {"street": "str", "city": "str"}}}'
        )
        model = build_model_from_schema_json(schema_json)
        instance = model(address={"street": "123 Main", "city": "Seoul"})
        assert instance.address.street == "123 Main"
        assert instance.address.city == "Seoul"

    def test_enum_field(self):
        schema_json = '{"status": {"type": "enum", "values": ["active", "inactive"]}}'
        model = build_model_from_schema_json(schema_json)
        instance = model(status="active")
        assert instance.status == "active"

    def test_enum_invalid_value_raises(self):
        schema_json = '{"status": {"type": "enum", "values": ["active", "inactive"]}}'
        model = build_model_from_schema_json(schema_json)
        with pytest.raises(Exception):
            model(status="unknown")

    def test_object_empty_properties_raises(self):
        schema_json = '{"address": {"type": "object", "properties": {}}}'
        with pytest.raises(ValueError, match="non-empty"):
            build_model_from_schema_json(schema_json)

    def test_enum_empty_values_raises(self):
        schema_json = '{"status": {"type": "enum", "values": []}}'
        with pytest.raises(ValueError, match="non-empty"):
            build_model_from_schema_json(schema_json)

    def test_list_unknown_items_type_raises(self):
        schema_json = '{"tags": {"type": "list", "items": "unknown"}}'
        with pytest.raises(ValueError, match="Unknown items type"):
            build_model_from_schema_json(schema_json)


# * _validate_source tests


class TestValidateSource:
    """Tests for file source validation."""

    def test_remote_sources_pass(self):
        for prefix in ("s3://", "gs://", "az://", "http://", "https://"):
            _validate_source(f"{prefix}bucket/file.xlsx")

    def test_nonexistent_local_file_raises(self):
        with pytest.raises(FileNotFoundError, match="File not found"):
            _validate_source("/nonexistent/path/to/file.xlsx")

    def test_directory_raises(self, tmp_path: PathLibPath):
        with pytest.raises(ValueError, match="not a file"):
            _validate_source(str(tmp_path))

    def test_existing_file_passes(self, tmp_path: PathLibPath):
        f = tmp_path / "test.xlsx"
        f.write_bytes(b"dummy")
        _validate_source(str(f))


# * MCP server tool registration tests


def _mcp_available() -> bool:
    try:
        import mcp  # noqa: F401

        return True
    except ImportError:
        return False


_skip_no_mcp = pytest.mark.skipif(not _mcp_available(), reason="mcp package not installed")


@_skip_no_mcp
class TestMcpServerToolRegistration:
    """Tests that all 7 tools are registered on the MCP server."""

    @pytest.fixture
    def mcp(self):
        return create_mcp_server()

    def test_server_name(self, mcp: Any):
        assert mcp.name == "xlstruct"

    def test_all_tools_registered(self, mcp: Any):
        tool_names = set(mcp._tool_manager._tools.keys())
        expected = {
            "extract",
            "suggest_schema",
            "generate_script",
            "extract_batch",
            "inspect_sheet",
            "cache_list",
            "cache_clear",
        }
        assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"

    def test_tool_count(self, mcp: Any):
        assert len(mcp._tool_manager._tools) >= 7


# * Tool execution tests with mocks


@_skip_no_mcp
class TestExtractTool:
    """Tests for the extract tool with mocked Extractor."""

    @pytest.fixture
    def mcp(self):
        return create_mcp_server()

    async def test_extract_calls_extractor(self, mcp: Any, tmp_path: PathLibPath):
        """Verify extract tool calls Extractor.extract with correct args."""
        test_file = tmp_path / "test.xlsx"
        test_file.write_bytes(b"dummy")

        # * Create mock result
        mock_item = MagicMock()
        mock_item.model_dump.return_value = {"name": "Test", "value": 42}

        mock_usage = MagicMock()
        mock_usage.llm_calls = 1
        mock_usage.input_tokens = 100
        mock_usage.output_tokens = 50
        mock_usage.total_tokens = 150

        mock_report = MagicMock()
        mock_report.mode.value = "direct"
        mock_report.usage = mock_usage

        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([mock_item]))
        mock_result.report = mock_report

        with patch("xlstruct.mcp_server.Extractor") as MockExtractor:
            mock_extractor = MockExtractor.return_value
            mock_extractor.extract = AsyncMock(return_value=mock_result)

            # ^ Call the tool function directly
            tool_fn = mcp._tool_manager._tools["extract"].fn
            result_json = await tool_fn(
                source=str(test_file),
                schema_json='{"name": "str", "value": "int"}',
                provider=None,
                sheet=None,
                mode="auto",
                instructions=None,
            )

            result = json.loads(result_json)
            assert result["count"] == 1
            assert result["records"][0]["name"] == "Test"
            assert result["report"]["usage"]["total_tokens"] == 150


# * Cache tool tests


@_skip_no_mcp
class TestCacheTools:
    """Tests for cache_list and cache_clear tools."""

    @pytest.fixture
    def mcp(self):
        return create_mcp_server()

    async def test_cache_list_empty(self, mcp: Any):
        with patch("xlstruct.mcp_server.ScriptCache") as MockCache:
            MockCache.return_value.list_entries.return_value = []

            tool_fn = mcp._tool_manager._tools["cache_list"].fn
            result_json = await tool_fn()
            result = json.loads(result_json)

            assert result["count"] == 0
            assert result["entries"] == []

    async def test_cache_list_with_entries(self, mcp: Any):
        mock_entry = MagicMock()
        mock_entry.model_dump.return_value = {
            "signature": "abc123",
            "schema_name": "InvoiceItem",
            "schema_fields": ["name", "amount"],
            "sheet_name": "Sheet1",
            "col_count": 5,
            "header_sample": ["Name", "Amount"],
            "created_at": "2025-01-01T00:00:00",
            "explanation": "Parses invoice items",
        }

        with patch("xlstruct.mcp_server.ScriptCache") as MockCache:
            MockCache.return_value.list_entries.return_value = [mock_entry]

            tool_fn = mcp._tool_manager._tools["cache_list"].fn
            result_json = await tool_fn()
            result = json.loads(result_json)

            assert result["count"] == 1
            assert result["entries"][0]["signature"] == "abc123"

    async def test_cache_clear_all(self, mcp: Any):
        with patch("xlstruct.mcp_server.ScriptCache") as MockCache:
            MockCache.return_value.clear.return_value = 3

            tool_fn = mcp._tool_manager._tools["cache_clear"].fn
            result_json = await tool_fn(signature=None)
            result = json.loads(result_json)

            assert result["action"] == "clear_all"
            assert result["removed_count"] == 3

    async def test_cache_clear_specific(self, mcp: Any):
        with patch("xlstruct.mcp_server.ScriptCache") as MockCache:
            MockCache.return_value.remove.return_value = True

            tool_fn = mcp._tool_manager._tools["cache_clear"].fn
            result_json = await tool_fn(signature="abc123")
            result = json.loads(result_json)

            assert result["action"] == "remove"
            assert result["signature"] == "abc123"
            assert result["removed"] is True
