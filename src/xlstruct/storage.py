"""Storage abstraction using fsspec.

Provides async file reading from any fsspec-supported backend:
local, s3://, az://, gs://, etc.
"""

import asyncio
from typing import IO, Any, cast

import fsspec

from xlstruct.exceptions import ErrorCode, StorageError


async def read_file(source: str, **storage_options: Any) -> bytes:
    """Read a file from any fsspec-supported location.

    Args:
        source: File path or URL (local, s3://, az://, gs://)
        **storage_options: Backend-specific options (credentials, etc.)

    Returns:
        File contents as bytes.

    Raises:
        StorageError: If the file cannot be read.
    """

    def _sync_read() -> bytes:
        with fsspec.open(source, mode="rb", **storage_options) as f:  # type: ignore
            return bytes(cast(IO[bytes], f).read())

    try:
        # ^ fsspec is sync-only for most backends; to_thread is safest async pattern
        return await asyncio.to_thread(_sync_read)
    except FileNotFoundError as e:
        raise StorageError(f"File not found: {source}", code=ErrorCode.STORAGE_NOT_FOUND) from e
    except PermissionError as e:
        raise StorageError(
            f"Permission denied: {source}", code=ErrorCode.STORAGE_PERMISSION_DENIED
        ) from e
    except OSError as e:
        raise StorageError(
            f"Failed to read file: {source} — {e}", code=ErrorCode.STORAGE_READ_FAILED
        ) from e
