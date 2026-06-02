"""Script cache for codegen mode.

Caches generated scripts by sheet structure signature so that
files with the same layout can reuse a previously generated script
without additional LLM calls.

Cached scripts are executed on a hit, so the cache is integrity-protected: the
directory is private (0700), entries are private (0600), and each script carries
an HMAC keyed by a per-user secret. Entries that fail verification are refused
(never executed) and regenerated.
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path as PathLibPath

from pydantic import BaseModel

from xlstruct.schemas.codegen import GeneratedScript
from xlstruct.schemas.core import SheetData

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = PathLibPath.home() / ".xlstruct" / "cache"

# ^ Per-user HMAC key, co-located in the (0700) cache dir so only the owner can read it.
_SECRET_FILENAME = ".hmac_key"


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
    mac: str = ""  # ^ HMAC over (signature, code); "" marks a legacy/unverified entry


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

    Returns the full 256-bit SHA-256 hex digest (not truncated) so the cache
    key is not feasibly predictable/collidable from the inputs.
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

    return hashlib.sha256("\n".join(components).encode()).hexdigest()


def _compute_mac(secret: bytes, signature: str, code: str) -> str:
    """HMAC-SHA256 over the signature-bound script bytes."""
    msg = signature.encode("utf-8") + b"\n" + code.encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def _is_hex_sha256(value: str) -> bool:
    """True if ``value`` is a 64-char lowercase hex digest.

    Guards ``hmac.compare_digest`` against a non-ASCII ``mac`` from attacker-controlled
    JSON (it raises TypeError on non-ASCII str), so a malformed value is treated as a
    verification failure rather than crashing extraction.
    """
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


class ScriptCache:
    """File-based cache for generated codegen scripts (integrity-protected)."""

    def __init__(self, cache_dir: PathLibPath | None = None) -> None:
        self._cache_dir = cache_dir or DEFAULT_CACHE_DIR

    @property
    def cache_dir(self) -> PathLibPath:
        return self._cache_dir

    # * Integrity helpers

    def _ensure_dir(self) -> None:
        """Create the cache dir and lock it to owner-only (0700)."""
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._cache_dir, 0o700)
        except OSError as e:
            # ^ Observable boundary: perms may be unsupported (e.g. Windows); HMAC still applies.
            logger.warning("Could not restrict cache dir permissions to 0700: %s", e)

    def _get_secret(self) -> bytes:
        """Load (or create) the per-user HMAC key stored 0600 in the cache dir."""
        self._ensure_dir()
        secret_path = self._cache_dir / _SECRET_FILENAME
        if secret_path.exists():
            try:
                os.chmod(secret_path, 0o600)
            except OSError:
                pass
            return secret_path.read_bytes()

        key = secrets.token_bytes(32)
        try:
            fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            # ^ Race: another process created it first — use theirs.
            return secret_path.read_bytes()
        with os.fdopen(fd, "wb") as f:
            f.write(key)
        return key

    @staticmethod
    def _write_private(path: PathLibPath, data: str) -> None:
        """Write text to ``path`` with owner-only (0600) permissions."""
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        try:
            os.chmod(path, 0o600)  # ^ enforce 0600 even when the file pre-existed
        except OSError:
            pass

    # * Public API

    def get(self, signature: str) -> GeneratedScript | None:
        """Look up a cached script by structure signature.

        Returns None on a miss OR when integrity verification fails — a failed
        entry is refused (never executed) and the caller regenerates.
        """
        script_path = self._cache_dir / f"{signature}.py"
        meta_path = self._cache_dir / f"{signature}.json"

        if not script_path.exists() or not meta_path.exists():
            return None

        try:
            code = script_path.read_text(encoding="utf-8")
            meta_raw = json.loads(meta_path.read_text(encoding="utf-8"))
            meta = CacheMetadata.model_validate(meta_raw)
        except Exception as e:
            logger.warning("Cache read failed for %s: %s", signature, e)
            return None

        # * Integrity gate — refuse to return code we cannot verify. Any failure here
        #   (malformed/non-ASCII mac, unreadable secret) degrades to a clean miss, never
        #   a crash, preserving the skip+regenerate contract against hostile cache files.
        try:
            verified = _is_hex_sha256(meta.mac) and hmac.compare_digest(
                meta.mac, _compute_mac(self._get_secret(), signature, code)
            )
        except Exception as e:
            logger.warning("Cache integrity check errored for %s: %s", signature, e)
            verified = False

        if not verified:
            logger.warning(
                "Cache integrity check FAILED for %s — refusing to load the cached script "
                "(it will not be executed). Regenerating.",
                signature,
            )
            return None

        logger.info("Cache hit: %s (created %s)", signature, meta.created_at)
        return GeneratedScript(code=code, explanation=meta.explanation)

    def put(
        self,
        signature: str,
        script: GeneratedScript,
        sheet: SheetData,
        header_rows: list[int],
        schema: type[BaseModel],
    ) -> PathLibPath:
        """Store a script in the cache (private perms + integrity MAC)."""
        self._ensure_dir()
        secret = self._get_secret()

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
            mac=_compute_mac(secret, signature, script.code),
        )

        self._write_private(script_path, script.code)
        self._write_private(meta_path, meta.model_dump_json(indent=2))
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
