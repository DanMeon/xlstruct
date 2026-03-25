"""ExtractionEngine: Instructor-based async LLM extraction."""

from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field, create_model

from xlstruct.config import ExtractorConfig, apply_cache_control, build_instructor_client
from xlstruct.exceptions import ErrorCode, ExtractionError
from xlstruct.prompts.extraction import build_extraction_prompt
from xlstruct.prompts.system import SYSTEM_PROMPT
from xlstruct.schemas.usage import UsageTracker

T = TypeVar("T", bound=BaseModel)

# * Confidence level type and score mapping
ConfidenceLevel = Literal["very_high", "high", "moderate", "low", "very_low"]

CONFIDENCE_SCORES: dict[str, float] = {
    "very_high": 1.0,
    "high": 0.75,
    "moderate": 0.5,
    "low": 0.25,
    "very_low": 0.0,
}


def _build_provenance_schema(schema: type[T]) -> type[BaseModel]:
    """Dynamically create a wrapper schema that includes source_rows and source_cells."""
    field_definitions: dict[str, Any] = {}
    for name, info in schema.model_fields.items():
        field_definitions[name] = (info.annotation, info)
    field_definitions["source_rows"] = (
        list[int],
        Field(
            description="1-indexed Excel row number(s) this record was extracted from. "
            "Use the Row column in the spreadsheet data.",
        ),
    )
    field_definitions["source_cells"] = (
        dict[str, str],
        Field(
            description='Mapping of output field name to cell address (e.g. "A5", "C14"). '
            "Use column letters from the table header and row numbers from the Row column.",
        ),
    )
    return create_model(f"{schema.__name__}WithProvenance", **field_definitions)


def _build_confidence_schema(
    schema: type[T],
    exclude_fields: set[str] | None = None,
) -> type[BaseModel]:
    """Dynamically create a wrapper schema that adds per-field confidence levels.

    Args:
        schema: The base schema (possibly already wrapped with provenance fields).
        exclude_fields: Field names to carry over without adding confidence counterparts.
            Used to skip provenance-added fields like ``source_rows``.
    """
    _exclude = exclude_fields or set()
    field_definitions: dict[str, Any] = {}
    for name, info in schema.model_fields.items():
        field_definitions[name] = (info.annotation, info)
        if name in _exclude:
            continue
        field_definitions[f"{name}_confidence"] = (
            ConfidenceLevel,
            Field(
                description=f"Confidence level for '{name}': very_high (certain from clear cell data), "
                "high (strong inference), moderate (reasonable guess), "
                "low (uncertain), very_low (mostly guessing).",
            ),
        )
    return create_model(f"{schema.__name__}WithConfidence", **field_definitions)


def _split_confidence(
    items: list[BaseModel], original_schema: type[T]
) -> tuple[list[T], dict[str, list[float]]]:
    """Strip confidence fields from wrapper records, convert to numeric scores.

    Returns:
        Tuple of (clean records rebuilt as original_schema, field_confidences dict
        mapping field_name → list of numeric scores, one per record).
    """
    field_names = list(original_schema.model_fields.keys())
    field_confidences: dict[str, list[float]] = {name: [] for name in field_names}

    cleaned: list[T] = []
    for item in items:
        data = item.model_dump()

        # ^ Extract and convert confidence scores
        for name in field_names:
            conf_key = f"{name}_confidence"
            level = data.pop(conf_key, "moderate")
            field_confidences[name].append(CONFIDENCE_SCORES.get(level, 0.5))

        record = original_schema.model_validate(data)
        cleaned.append(record)

    return cleaned, field_confidences


class ExtractionEngine:
    """Wraps Instructor to extract structured data from encoded sheet text."""

    def __init__(self, config: ExtractorConfig, tracker: UsageTracker | None = None) -> None:
        self._config = config
        self._tracker = tracker
        self._client = build_instructor_client(config)

    async def extract(
        self,
        encoded_text: str,
        schema: type[T],
        instructions: str | None = None,
        *,
        is_sampled: bool = False,
        total_rows: int | None = None,
        track_provenance: bool = False,
        include_confidence: bool = False,
    ) -> list[T]:
        """Extract structured data matching schema from encoded sheet text.

        Always returns list[T] (single record = list of length 1).
        When track_provenance is True, returns records with source_rows stripped
        and stored separately.
        When include_confidence is True, returns records with confidence fields stripped
        and stored as _field_confidences attribute.
        """
        prompt = build_extraction_prompt(
            encoded_text,
            instructions,
            is_sampled=is_sampled,
            total_rows=total_rows,
            track_provenance=track_provenance,
            include_confidence=include_confidence,
        )

        # ^ Use wrapper schema when provenance/confidence is requested
        response_schema: type[BaseModel] = schema
        if track_provenance:
            response_schema = _build_provenance_schema(response_schema)
        if include_confidence:
            # ^ Exclude provenance-added fields so they don't get confidence counterparts
            provenance_fields = {"source_rows"} if track_provenance else None
            response_schema = _build_confidence_schema(
                response_schema, exclude_fields=provenance_fields
            )

        try:
            messages = apply_cache_control(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                self._config.provider,
            )
            result, completion = await self._client.create_with_completion(
                response_model=list[response_schema],  # type: ignore[valid-type]
                messages=messages,
                max_retries=self._config.max_retries,
                temperature=self._config.temperature,
            )
            if self._tracker:
                self._tracker.record("extraction", completion)

            items = list(result)

            # ^ Split confidence first (outermost wrapper), then provenance
            field_confidences: dict[str, list[float]] | None = None
            if include_confidence:
                # ^ The schema to split against includes provenance fields if both are enabled
                split_schema = _build_provenance_schema(schema) if track_provenance else schema
                items, field_confidences = _split_confidence(items, split_schema)

            if track_provenance:
                items = self._split_provenance(items, schema)

            # ^ Attach confidence data to each record for collection by Extractor
            if field_confidences is not None:
                for i, item in enumerate(items):
                    per_record: dict[str, float] = {}
                    for field_name, scores in field_confidences.items():
                        if i < len(scores):
                            per_record[field_name] = scores[i]
                    item._field_confidences = per_record

            return items
        except Exception as e:
            raise ExtractionError(
                f"LLM extraction failed: {e}", code=ErrorCode.EXTRACTION_LLM_FAILED
            ) from e

    @staticmethod
    def _split_provenance(items: list[BaseModel], original_schema: type[T]) -> list[T]:
        """Strip provenance fields from wrapper records, rebuild as original schema.

        Stores source_rows and source_cells on each record as private attributes
        for later collection by the Extractor.
        """
        cleaned: list[T] = []
        for item in items:
            data = item.model_dump()
            source_rows = data.pop("source_rows", [])
            source_cells = data.pop("source_cells", {})
            record = original_schema.model_validate(data)
            record._source_rows = source_rows  # type: ignore[attr-defined]
            record._source_cells = source_cells  # type: ignore[attr-defined]
            cleaned.append(record)
        return cleaned
