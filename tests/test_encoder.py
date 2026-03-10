"""Tests for CompressedEncoder."""

from xlstruct.encoder.compressed import CompressedEncoder
from xlstruct.schemas.core import CellData, SheetData


class TestCompressedEncoderFull:
    """Full mode (no sampling) — all rows included."""

    def test_encode_simple(self, simple_sheet: SheetData):
        encoder = CompressedEncoder()
        result = encoder.encode(simple_sheet)

        assert "Inventory" in result
        assert "Item" in result
        assert "WDG-001" in result
        assert "Widget Alpha" in result
        # ^ Should be a markdown table
        assert "|" in result
        # ^ Should include metadata
        assert "Column Types" in result
        assert "Stats" in result

    def test_encode_merged(self, merged_sheet: SheetData):
        encoder = CompressedEncoder()
        result = encoder.encode(merged_sheet)

        assert "Invoice" in result
        assert "Merged Regions" in result
        assert "Invoice #2024-001" in result

    def test_encode_empty(self):
        sheet = SheetData(name="empty", row_count=0, col_count=0)
        result = CompressedEncoder().encode(sheet)
        assert "empty" in result.lower()


class TestCompressedEncoderSampled:
    """Sampled mode — only N rows included."""

    def test_sample_limits_rows(self):
        cells = [
            CellData(row=1, col=1, value="Header"),
            *[CellData(row=i, col=1, value=f"row{i}") for i in range(2, 52)],
        ]
        sheet = SheetData(name="big", row_count=51, col_count=1, cells=cells)

        encoder = CompressedEncoder(sample_size=10)
        result = encoder.encode(sheet)

        assert "sample 10 of" in result
        # ^ Should have head + tail sampling
        assert "row2" in result   # ^ head
        assert "row51" in result  # ^ tail
        assert "..." in result    # ^ gap indicator

    def test_no_sample_when_fewer_rows(self, simple_sheet: SheetData):
        encoder = CompressedEncoder(sample_size=100)
        result = encoder.encode(simple_sheet)

        # ^ All rows included, no "sample" label
        assert "sample" not in result.lower()

    def test_form_style_no_false_sampling(self):
        """Form-style sheet: many non-empty rows above data, but few data rows."""
        cells = [
            # ^ Form header area (rows 1-18)
            *[CellData(row=i, col=1, value=f"form_field_{i}") for i in range(1, 19)],
            # ^ Table header (row 19)
            CellData(row=19, col=1, value="Item"),
            CellData(row=19, col=2, value="Qty"),
            # ^ Data rows (rows 20-34 = 15 rows)
            *[
                cell
                for i in range(20, 35)
                for cell in [
                    CellData(row=i, col=1, value=f"item_{i}"),
                    CellData(row=i, col=2, value=i * 10),
                ]
            ],
        ]
        sheet = SheetData(name="invoice", row_count=34, col_count=2, cells=cells)

        encoder = CompressedEncoder(sample_size=20)
        result = encoder.encode(sheet, header_rows=[19])

        # ^ 15 data rows < sample_size=20, so no sampling should occur
        assert "sample" not in result.lower()
        # ^ All 15 data rows should be present
        assert "item_20" in result
        assert "item_34" in result
