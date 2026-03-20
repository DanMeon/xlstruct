"""Tests for codegen script caching."""

import json

import pytest
from pydantic import BaseModel

from xlstruct.codegen.cache import (
    CacheMetadata,
    ScriptCache,
    compute_structure_signature,
)
from xlstruct.schemas.codegen import GeneratedScript
from xlstruct.schemas.core import CellData, SheetData

# * Test schemas


class Invoice(BaseModel):
    customer: str
    amount: float


class Order(BaseModel):
    product: str
    quantity: int
    price: float


# * Fixtures


@pytest.fixture
def sample_sheet() -> SheetData:
    """Sheet with header row and a few data rows."""
    return SheetData(
        name="Sheet1",
        row_count=4,
        col_count=2,
        cells=[
            CellData(row=1, col=1, value="Customer"),
            CellData(row=1, col=2, value="Amount"),
            CellData(row=2, col=1, value="Alice"),
            CellData(row=2, col=2, value=100.0),
            CellData(row=3, col=1, value="Bob"),
            CellData(row=3, col=2, value=200.0),
        ],
    )


@pytest.fixture
def sample_script() -> GeneratedScript:
    return GeneratedScript(
        code='import openpyxl\nprint("hello")',
        explanation="Parses customer and amount columns",
    )


@pytest.fixture
def cache(tmp_path) -> ScriptCache:
    return ScriptCache(cache_dir=tmp_path / "cache")


# * Signature tests


class TestComputeStructureSignature:
    def test_deterministic(self, sample_sheet):
        """Same inputs produce the same signature."""
        sig1 = compute_structure_signature(sample_sheet, [1], Invoice)
        sig2 = compute_structure_signature(sample_sheet, [1], Invoice)
        assert sig1 == sig2

    def test_length(self, sample_sheet):
        """Signature is 16-char hex string."""
        sig = compute_structure_signature(sample_sheet, [1], Invoice)
        assert len(sig) == 16
        assert all(c in "0123456789abcdef" for c in sig)

    def test_different_schema_different_signature(self, sample_sheet):
        """Different output schema → different signature."""
        sig_invoice = compute_structure_signature(sample_sheet, [1], Invoice)
        sig_order = compute_structure_signature(sample_sheet, [1], Order)
        assert sig_invoice != sig_order

    def test_different_headers_different_signature(self, sample_sheet):
        """Different header rows → different signature."""
        sig1 = compute_structure_signature(sample_sheet, [1], Invoice)
        sig2 = compute_structure_signature(sample_sheet, [1, 2], Invoice)
        assert sig1 != sig2

    def test_different_col_count_different_signature(self, sample_sheet):
        """Different column count → different signature."""
        sig1 = compute_structure_signature(sample_sheet, [1], Invoice)

        modified = sample_sheet.model_copy(update={"col_count": 5})
        sig2 = compute_structure_signature(modified, [1], Invoice)
        assert sig1 != sig2

    def test_different_header_values_different_signature(self):
        """Different header cell values → different signature."""
        sheet_a = SheetData(
            name="A",
            row_count=2,
            col_count=2,
            cells=[
                CellData(row=1, col=1, value="Name"),
                CellData(row=1, col=2, value="Price"),
            ],
        )
        sheet_b = SheetData(
            name="B",
            row_count=2,
            col_count=2,
            cells=[
                CellData(row=1, col=1, value="Product"),
                CellData(row=1, col=2, value="Cost"),
            ],
        )
        sig_a = compute_structure_signature(sheet_a, [1], Invoice)
        sig_b = compute_structure_signature(sheet_b, [1], Invoice)
        assert sig_a != sig_b


# * ScriptCache tests


class TestScriptCache:
    def test_get_miss(self, cache):
        """Cache miss returns None."""
        assert cache.get("nonexistent") is None

    def test_put_and_get(self, cache, sample_sheet, sample_script):
        """Put a script then retrieve it."""
        sig = "abc123def456abcd"
        cache.put(sig, sample_script, sample_sheet, [1], Invoice)

        result = cache.get(sig)
        assert result is not None
        assert result.code == sample_script.code
        assert result.explanation == sample_script.explanation

    def test_put_creates_directory(self, tmp_path, sample_sheet, sample_script):
        """Cache directory is created on first put."""
        cache_dir = tmp_path / "nested" / "cache"
        cache = ScriptCache(cache_dir=cache_dir)

        cache.put("sig123", sample_script, sample_sheet, [1], Invoice)
        assert cache_dir.exists()

    def test_metadata_written(self, cache, sample_sheet, sample_script):
        """Metadata JSON is valid and contains expected fields."""
        sig = "meta_test_123456"
        cache.put(sig, sample_script, sample_sheet, [1], Invoice)

        meta_path = cache.cache_dir / f"{sig}.json"
        assert meta_path.exists()

        meta_raw = json.loads(meta_path.read_text())
        meta = CacheMetadata.model_validate(meta_raw)
        assert meta.signature == sig
        assert meta.schema_name == "Invoice"
        assert "customer" in meta.schema_fields
        assert "amount" in meta.schema_fields
        assert meta.sheet_name == "Sheet1"

    def test_remove_existing(self, cache, sample_sheet, sample_script):
        """Remove a cached entry."""
        sig = "to_remove_123456"
        cache.put(sig, sample_script, sample_sheet, [1], Invoice)
        assert cache.get(sig) is not None

        removed = cache.remove(sig)
        assert removed is True
        assert cache.get(sig) is None

    def test_remove_nonexistent(self, cache):
        """Remove returns False for nonexistent entry."""
        assert cache.remove("does_not_exist") is False

    def test_clear(self, cache, sample_sheet, sample_script):
        """Clear removes all entries."""
        cache.put("sig_a_123456abcd", sample_script, sample_sheet, [1], Invoice)
        cache.put("sig_b_123456abcd", sample_script, sample_sheet, [1], Invoice)

        count = cache.clear()
        assert count == 2
        assert cache.get("sig_a_123456abcd") is None
        assert cache.get("sig_b_123456abcd") is None

    def test_clear_empty(self, cache):
        """Clear on empty/nonexistent cache returns 0."""
        assert cache.clear() == 0

    def test_list_entries(self, cache, sample_sheet, sample_script):
        """List returns all cached metadata."""
        cache.put("list_a_12345678", sample_script, sample_sheet, [1], Invoice)
        cache.put("list_b_12345678", sample_script, sample_sheet, [1], Invoice)

        entries = cache.list_entries()
        assert len(entries) == 2
        sigs = {e.signature for e in entries}
        assert sigs == {"list_a_12345678", "list_b_12345678"}

    def test_list_entries_empty(self, cache):
        """List on empty cache returns empty list."""
        assert cache.list_entries() == []

    def test_corrupted_metadata_returns_none(self, cache, sample_sheet, sample_script):
        """Corrupted metadata file → cache miss (not crash)."""
        sig = "corrupt_1234abcd"
        cache.put(sig, sample_script, sample_sheet, [1], Invoice)

        # ^ Corrupt the metadata
        meta_path = cache.cache_dir / f"{sig}.json"
        meta_path.write_text("not valid json!!!", encoding="utf-8")

        result = cache.get(sig)
        assert result is None

    def test_missing_script_file_returns_none(self, cache, sample_sheet, sample_script):
        """Missing .py file with existing .json → cache miss."""
        sig = "missing_py_12345"
        cache.put(sig, sample_script, sample_sheet, [1], Invoice)

        # ^ Delete the script file
        script_path = cache.cache_dir / f"{sig}.py"
        script_path.unlink()

        result = cache.get(sig)
        assert result is None
