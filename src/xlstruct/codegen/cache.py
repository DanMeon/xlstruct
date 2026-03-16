"""Script cache for codegen mode.

Caches generated scripts by sheet structure signature so that
files with the same layout can reuse a previously generated script
without additional LLM calls.
"""

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path as PathLibPath

from pydantic import BaseModel

from xlstruct.schemas.codegen import GeneratedScript
from xlstruct.schemas.core import SheetData

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = PathLibPath.home() / ".xlstruct" / "cache"


class CacheMetadata(BaseModel):
    """Metadata stored alongside a cached script."""

    signature: str
    schema_name: str
    schema_fields: list[str]
    sheet_name: str
    col_count: int
    header_sample: list[str]
    created_at: str
    explanation: str


def compute_structure_signature(
    sheet: SheetData,
    header_rows: list[int],
    schema: type[BaseModel],
) -> str:
    """Compute a hash signature from sheet structure + output schema.

    The signature captures:
    - Header cell values (column names)
    - Column count
    - Schema field names and types
    """
    # * Collect header cell values
    header_values: list[str] = []
    for cell in sheet.cells:
        if cell.row in header_rows and cell.value is not None:
            header_values.append(f"{cell.row}:{cell.col}={cell.value}")
    header_values.sort()

    # * Schema field signature
    field_sig: list[str] = []
    for name, field_info in sorted(schema.model_fields.items()):
        annotation = field_info.annotation
        type_name = getattr(annotation, "__name__", str(annotation))
        field_sig.append(f"{name}:{type_name}")

    components = [
        "|".join(header_values),
        str(sheet.col_count),
        "|".join(field_sig),
    ]

    return hashlib.sha256("\n".join(components).encode()).hexdigest()[:16]


class ScriptCache:
    """File-based cache for generated codegen scripts."""

    def __init__(self, cache_dir: PathLibPath | None = None) -> None:
        self._cache_dir = cache_dir or DEFAULT_CACHE_DIR

    @property
    def cache_dir(self) -> PathLibPath:
        return self._cache_dir

    def get(self, signature: str) -> GeneratedScript | None:
        """Look up a cached script by structure signature."""
        script_path = self._cache_dir / f"{signature}.py"
        meta_path = self._cache_dir / f"{signature}.json"

        if not script_path.exists() or not meta_path.exists():
            return None

        try:
            code = script_path.read_text(encoding="utf-8")
            meta_raw = json.loads(meta_path.read_text(encoding="utf-8"))
            meta = CacheMetadata.model_validate(meta_raw)
            logger.info("Cache hit: %s (created %s)", signature, meta.created_at)
            return GeneratedScript(code=code, explanation=meta.explanation)
        except Exception as e:
            logger.warning("Cache read failed for %s: %s", signature, e)
            return None

    def put(
        self,
        signature: str,
        script: GeneratedScript,
        sheet: SheetData,
        header_rows: list[int],
        schema: type[BaseModel],
    ) -> PathLibPath:
        """Store a script in the cache."""
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        script_path = self._cache_dir / f"{signature}.py"
        meta_path = self._cache_dir / f"{signature}.json"

        # * Collect header sample for metadata
        header_sample: list[str] = []
        for cell in sheet.cells:
            if cell.row in header_rows and cell.value is not None:
                header_sample.append(str(cell.value))

        meta = CacheMetadata(
            signature=signature,
            schema_name=schema.__name__,
            schema_fields=list(schema.model_fields.keys()),
            sheet_name=sheet.name,
            col_count=sheet.col_count,
            header_sample=header_sample,
            created_at=datetime.now(UTC).isoformat(),
            explanation=script.explanation,
        )

        script_path.write_text(script.code, encoding="utf-8")
        meta_path.write_text(meta.model_dump_json(indent=2), encoding="utf-8")
        logger.info("Cached script: %s → %s", signature, script_path)
        return script_path

    def remove(self, signature: str) -> bool:
        """Remove a cached script by signature."""
        script_path = self._cache_dir / f"{signature}.py"
        meta_path = self._cache_dir / f"{signature}.json"
        removed = False
        for path in (script_path, meta_path):
            if path.exists():
                path.unlink()
                removed = True
        return removed

    def clear(self) -> int:
        """Remove all cached scripts. Returns number of entries removed."""
        if not self._cache_dir.exists():
            return 0
        count = 0
        for path in self._cache_dir.iterdir():
            if path.suffix in (".py", ".json"):
                path.unlink()
                count += 1
        return count // 2  # ^ Each entry has .py + .json

    def list_entries(self) -> list[CacheMetadata]:
        """List all cached entries with metadata."""
        if not self._cache_dir.exists():
            return []
        entries: list[CacheMetadata] = []
        for meta_path in sorted(self._cache_dir.glob("*.json")):
            try:
                meta_raw = json.loads(meta_path.read_text(encoding="utf-8"))
                entries.append(CacheMetadata.model_validate(meta_raw))
            except Exception:
                continue
        return entries
