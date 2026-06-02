"""Assemble an ExtractionReport from extracted records.

Pulls provenance (``_source_rows`` / ``_source_cells``) and per-field confidence
(``_field_confidences``) that ``ExtractionEngine`` attaches to records, and folds
them into the report. Extracted from ``Extractor.extract`` to keep that method
a thin orchestrator.
"""

from typing import Any

from xlstruct.config import ExtractionMode
from xlstruct.schemas.report import ExtractionReport
from xlstruct.schemas.usage import TokenUsage


def build_extraction_report(
    items: list[Any],
    mode: ExtractionMode,
    usage: TokenUsage,
) -> ExtractionReport:
    """Build an ExtractionReport, folding in provenance/confidence from records.

    Args:
        items: Extracted records (each may carry ``_source_rows`` / ``_source_cells`` /
            ``_field_confidences`` attributes set by the engine).
        mode: The extraction mode that was actually used.
        usage: Token usage snapshot for this extraction.
    """
    # * Provenance — parallel to items; dropped entirely if nothing was tracked
    source_rows: list[list[int]] = [getattr(item, "_source_rows", []) for item in items]
    source_cells: list[dict[str, str]] = [getattr(item, "_source_cells", {}) for item in items]
    if not any(source_rows):
        source_rows = []
    if not any(source_cells):
        source_cells = []

    # * Confidence — only when the engine attached per-field scores
    field_confidences: dict[str, list[float]] | None = None
    if items and hasattr(items[0], "_field_confidences"):
        all_fields: set[str] = set()
        for item in items:
            per_record = getattr(item, "_field_confidences", {})
            all_fields.update(per_record.keys())
        field_confidences = {name: [] for name in sorted(all_fields)}
        for item in items:
            per_record = getattr(item, "_field_confidences", {})
            for name in field_confidences:
                field_confidences[name].append(per_record.get(name, 0.5))

    return ExtractionReport(
        mode=mode,
        usage=usage,
        source_rows=source_rows,
        source_cells=source_cells,
        field_confidences=field_confidences,
    )
