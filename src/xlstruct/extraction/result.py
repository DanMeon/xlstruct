"""ExtractionResult: list[T] carrying an attached ExtractionReport.

Lives in its own module so both the Extractor facade and the ExtractionPipeline
can construct it without a circular import.
"""

from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from pandas import DataFrame  # type: ignore

from pydantic import BaseModel

from xlstruct.schemas.report import ExtractionReport

T = TypeVar("T", bound=BaseModel)


class ExtractionResult(list[T]):  # type: ignore
    """List of extracted records with an attached extraction report.

    Behaves exactly like list[T] (iteration, indexing, len, etc.)
    but also exposes a ``.report`` attribute containing extraction metadata
    (mode used, token usage, provenance, etc.).
    """

    report: ExtractionReport

    def __init__(self, items: list[T], report: ExtractionReport) -> None:
        super().__init__(items)
        self.report = report

    def to_dataframe(self) -> "DataFrame":
        """Convert extracted records to a pandas DataFrame.

        Requires pandas to be installed: ``pip install xlstruct[pandas]``

        Returns:
            pandas DataFrame with one row per extracted record.
        """
        try:
            import pandas as pd
        except ImportError:
            raise ImportError(
                "pandas is required for to_dataframe(). "
                "Install it with: pip install xlstruct[pandas]"
            ) from None

        return pd.DataFrame([item.model_dump() for item in self])
