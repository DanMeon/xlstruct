"""Tests for per-field confidence scoring feature."""

import pytest
from pydantic import BaseModel

from xlstruct.extraction.engine import (
    CONFIDENCE_SCORES,
    _build_confidence_schema,
    _build_provenance_schema,
    _split_confidence,
)
from xlstruct.prompts.extraction import build_extraction_prompt

# * Test schemas


class Invoice(BaseModel):
    item_name: str
    qty: float


class SimpleRecord(BaseModel):
    name: str
    value: int
    active: bool


# * Schema wrapping tests


class TestBuildConfidenceSchema:
    def test_adds_confidence_fields(self) -> None:
        """Wrapped schema should have original fields + confidence fields."""
        wrapped = _build_confidence_schema(Invoice)
        field_names = set(wrapped.model_fields.keys())

        assert "item_name" in field_names
        assert "qty" in field_names
        assert "item_name_confidence" in field_names
        assert "qty_confidence" in field_names

    def test_confidence_field_count(self) -> None:
        """Wrapped schema should have exactly 2x the original field count."""
        wrapped = _build_confidence_schema(Invoice)
        assert len(wrapped.model_fields) == 2 * len(Invoice.model_fields)

    def test_confidence_field_type_is_literal(self) -> None:
        """Confidence fields should accept the 5-level Literal type."""
        wrapped = _build_confidence_schema(Invoice)
        # ^ Validate that the schema accepts valid confidence levels
        instance = wrapped.model_validate(
            {
                "item_name": "Widget",
                "item_name_confidence": "very_high",
                "qty": 10.0,
                "qty_confidence": "low",
            }
        )
        assert instance.item_name_confidence == "very_high"  # type: ignore
        assert instance.qty_confidence == "low"  # type: ignore

    def test_rejects_invalid_confidence_level(self) -> None:
        """Confidence fields should reject values outside the Literal enum."""
        wrapped = _build_confidence_schema(Invoice)
        with pytest.raises(Exception):
            wrapped.model_validate(
                {
                    "item_name": "Widget",
                    "item_name_confidence": "super_high",
                    "qty": 10.0,
                    "qty_confidence": "high",
                }
            )

    def test_model_name_suffix(self) -> None:
        """Wrapped model should have WithConfidence suffix."""
        wrapped = _build_confidence_schema(Invoice)
        assert wrapped.__name__ == "InvoiceWithConfidence"

    def test_multiple_fields(self) -> None:
        """Works with schemas having more than two fields."""
        wrapped = _build_confidence_schema(SimpleRecord)
        assert len(wrapped.model_fields) == 6
        assert "name_confidence" in wrapped.model_fields
        assert "value_confidence" in wrapped.model_fields
        assert "active_confidence" in wrapped.model_fields

    def test_excludes_provenance_fields(self) -> None:
        """When both provenance and confidence are enabled, source_rows should NOT get a confidence field."""
        provenance_schema = _build_provenance_schema(Invoice)
        # ^ Simulate what extract() does: pass provenance field names as exclude_fields
        wrapped = _build_confidence_schema(provenance_schema, exclude_fields={"source_rows"})
        field_names = set(wrapped.model_fields.keys())

        # ^ Original fields should have confidence counterparts
        assert "item_name_confidence" in field_names
        assert "qty_confidence" in field_names

        # ^ source_rows should be present but NOT have a confidence counterpart
        assert "source_rows" in field_names
        assert "source_rows_confidence" not in field_names


# * Confidence splitting tests


class TestSplitConfidence:
    def test_splits_and_converts_scores(self) -> None:
        """Split should produce clean records and numeric confidence scores."""
        wrapped = _build_confidence_schema(Invoice)
        items = [
            wrapped.model_validate(
                {
                    "item_name": "Widget",
                    "item_name_confidence": "very_high",
                    "qty": 100.0,
                    "qty_confidence": "high",
                }
            ),
            wrapped.model_validate(
                {
                    "item_name": "Gadget",
                    "item_name_confidence": "moderate",
                    "qty": 50.0,
                    "qty_confidence": "low",
                }
            ),
        ]

        cleaned, confidences = _split_confidence(items, Invoice)

        # ^ Clean records should be Invoice instances without confidence fields
        assert len(cleaned) == 2
        assert isinstance(cleaned[0], Invoice)
        assert cleaned[0].item_name == "Widget"
        assert cleaned[0].qty == 100.0
        assert cleaned[1].item_name == "Gadget"
        assert cleaned[1].qty == 50.0

        # ^ Confidence dict maps field_name → list of scores
        assert confidences["item_name"] == [1.0, 0.5]
        assert confidences["qty"] == [0.75, 0.25]

    def test_all_confidence_levels(self) -> None:
        """All five confidence levels should map to correct numeric scores."""
        assert CONFIDENCE_SCORES["very_high"] == 1.0
        assert CONFIDENCE_SCORES["high"] == 0.75
        assert CONFIDENCE_SCORES["moderate"] == 0.5
        assert CONFIDENCE_SCORES["low"] == 0.25
        assert CONFIDENCE_SCORES["very_low"] == 0.0

    def test_empty_items(self) -> None:
        """Split with no items should return empty results."""
        cleaned, confidences = _split_confidence([], Invoice)
        assert cleaned == []
        assert confidences == {"item_name": [], "qty": []}

    def test_single_record(self) -> None:
        """Split works correctly with a single record."""
        wrapped = _build_confidence_schema(Invoice)
        items = [
            wrapped.model_validate(
                {
                    "item_name": "Foo",
                    "item_name_confidence": "very_low",
                    "qty": 1.0,
                    "qty_confidence": "very_high",
                }
            ),
        ]

        cleaned, confidences = _split_confidence(items, Invoice)
        assert len(cleaned) == 1
        assert confidences["item_name"] == [0.0]
        assert confidences["qty"] == [1.0]


# * Prompt tests


class TestConfidencePrompt:
    def test_confidence_prompt_included(self) -> None:
        """Prompt should include confidence instructions when enabled."""
        prompt = build_extraction_prompt("some data", include_confidence=True)
        assert "Field Confidence" in prompt
        assert "very_high" in prompt
        assert "very_low" in prompt

    def test_confidence_prompt_excluded_by_default(self) -> None:
        """Prompt should NOT include confidence instructions by default."""
        prompt = build_extraction_prompt("some data")
        assert "Field Confidence" not in prompt

    def test_confidence_and_provenance_together(self) -> None:
        """Both provenance and confidence sections can coexist."""
        prompt = build_extraction_prompt(
            "some data",
            track_provenance=True,
            include_confidence=True,
        )
        assert "Row Provenance" in prompt
        assert "Field Confidence" in prompt


# * Config default test


class TestConfidenceConfig:
    def test_default_is_false(self) -> None:
        """include_confidence should default to False."""
        from xlstruct.config import ExtractionConfig

        config = ExtractionConfig(output_schema=Invoice)
        assert config.include_confidence is False

    def test_can_enable(self) -> None:
        """include_confidence can be set to True."""
        from xlstruct.config import ExtractionConfig

        config = ExtractionConfig(output_schema=Invoice, include_confidence=True)
        assert config.include_confidence is True
