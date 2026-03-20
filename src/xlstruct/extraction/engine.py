"""ExtractionEngine: Instructor-based async LLM extraction."""

from typing import Any, TypeVar

from pydantic import BaseModel, Field, create_model

from xlstruct.config import ExtractorConfig, apply_cache_control, build_instructor_client
from xlstruct.exceptions import ExtractionError
from xlstruct.prompts.extraction import build_extraction_prompt
from xlstruct.prompts.system import SYSTEM_PROMPT
from xlstruct.schemas.usage import UsageTracker

T = TypeVar("T", bound=BaseModel)


def _build_provenance_schema(schema: type[T]) -> type[BaseModel]:
    """Dynamically create a wrapper schema that includes source_rows."""
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
    return create_model(f"{schema.__name__}WithProvenance", **field_definitions)


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
    ) -> list[T]:
        """Extract structured data matching schema from encoded sheet text.

        Always returns list[T] (single record = list of length 1).
        When track_provenance is True, returns records with source_rows stripped
        and stored separately.
        """
        prompt = build_extraction_prompt(
            encoded_text,
            instructions,
            is_sampled=is_sampled,
            total_rows=total_rows,
            track_provenance=track_provenance,
        )

        # ^ Use wrapper schema when provenance is requested
        response_schema: type[BaseModel] = schema
        if track_provenance:
            response_schema = _build_provenance_schema(schema)

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

            if track_provenance:
                return self._split_provenance(items, schema)

            return items
        except Exception as e:
            raise ExtractionError(f"LLM extraction failed: {e}") from e

    @staticmethod
    def _split_provenance(
        items: list[BaseModel], original_schema: type[T]
    ) -> list[T]:
        """Strip source_rows from wrapper records, rebuild as original schema.

        Stores source_rows on each record as _source_rows attribute for
        later collection by the Extractor.
        """
        cleaned: list[T] = []
        for item in items:
            data = item.model_dump()
            source_rows = data.pop("source_rows", [])
            record = original_schema.model_validate(data)
            record._source_rows = source_rows  # type: ignore[attr-defined]
            cleaned.append(record)
        return cleaned
