"""Extraction report — aggregated metadata for an extraction operation."""

from pydantic import BaseModel, Field

from xlstruct.config import ExtractionMode
from xlstruct.schemas.usage import TokenUsage


class ExtractionReport(BaseModel):
    """Report summarizing how an extraction was performed.

    Contains mode selection, token usage, and optional provenance data.
    Accessed via ``ExtractionResult.report``.
    """

    mode: ExtractionMode = Field(
        description="Extraction mode that was actually used (direct or codegen).",
    )
    usage: TokenUsage = Field(
        description="Token consumption breakdown for this extraction.",
    )
    source_rows: list[list[int]] = Field(
        default_factory=list,
        description="Per-record source row numbers from the original Excel file. "
        "Populated when track_provenance=True. Parallel to the result items list.",
    )
    source_cells: list[dict[str, str]] = Field(
        default_factory=list,
        description="Per-record cell address mapping (field_name -> cell address like 'A5'). "
        "Populated when track_provenance=True. Parallel to the result items list.",
    )
    field_confidences: dict[str, list[float]] | None = Field(
        default=None,
        description="Per-field confidence scores. Maps field_name → list of numeric "
        "scores (one per record). Populated when include_confidence=True. "
        "Scores: 1.0=very_high, 0.75=high, 0.5=moderate, 0.25=low, 0.0=very_low.",
    )

    def summary(self) -> str:
        """Human-readable summary of the extraction."""
        lines: list[str] = []
        lines.append("ExtractionReport")
        lines.append("-" * 40)
        lines.append(f"Mode:      {self.mode.value}")
        lines.append(
            f"Tokens:    {self.usage.total_tokens:,} "
            f"(input: {self.usage.input_tokens:,} / output: {self.usage.output_tokens:,})"
        )
        cache_parts: list[str] = []
        if self.usage.cache_creation_tokens:
            cache_parts.append(f"{self.usage.cache_creation_tokens:,} created")
        if self.usage.cache_read_tokens:
            cache_parts.append(f"{self.usage.cache_read_tokens:,} read")
        if cache_parts:
            lines.append(f"Cache:     {' / '.join(cache_parts)}")

        if self.source_rows:
            lines.append(f"Provenance: {len(self.source_rows)} records mapped")
        if self.source_cells:
            lines.append(f"Cell provenance: {len(self.source_cells)} records mapped")

        if self.field_confidences:
            avg_scores: list[str] = []
            for field_name, scores in self.field_confidences.items():
                if scores:
                    avg = sum(scores) / len(scores)
                    avg_scores.append(f"{field_name}={avg:.2f}")
            lines.append(f"Confidence: {', '.join(avg_scores)}")

        return "\n".join(lines)

    def __str__(self) -> str:
        return self.summary()
