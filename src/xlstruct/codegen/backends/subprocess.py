"""SubprocessBackend — hardened subprocess execution with resource limits."""

import asyncio
import logging
import os
import sys
import tempfile
from pathlib import Path as PathLibPath

logger = logging.getLogger(__name__)

# ^ Whitelist approach: only these env vars are passed to the subprocess
ALLOWED_ENV_KEYS = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "PYTHONPATH",
        "PYTHONHASHSEED",
        "VIRTUAL_ENV",
        "UV_CACHE_DIR",
        "TMPDIR",
        "TMP",
        "TEMP",
    }
)


def _build_safe_env() -> dict[str, str]:
    """Build environment dict using whitelist approach.

    Only explicitly allowed env vars are passed to the subprocess.
    This prevents leaking credentials, tokens, and other secrets.
    """
    return {k: v for k, v in os.environ.items() if k in ALLOWED_ENV_KEYS}


def _apply_resource_limits() -> None:
    """Apply resource limits to subprocess (Linux/macOS only).

    Called as preexec_fn in subprocess. Limits memory to 512MB,
    file descriptors to 64, max file size to 50MB.
    """
    try:
        import resource

        # ^ 512MB memory limit
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
        # ^ 64 file descriptors
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        # ^ 50MB max file write size
        resource.setrlimit(resource.RLIMIT_FSIZE, (50 * 1024**2, 50 * 1024**2))
    except (ImportError, ValueError, OSError) as e:
        logger.warning("Failed to set resource limits: %s", e)


class SubprocessBackend:
    """Execute scripts in a hardened subprocess.

    Security measures:
    - Credential environment variables stripped (whitelist approach)
    - Memory limit: 512MB (via setrlimit)
    - File descriptor limit: 64 (via setrlimit)
    - Max file write size: 50MB (via setrlimit)
    """

    async def execute(
        self,
        code: str,
        source_path: str,
        timeout: int,
    ) -> tuple[int, str, str]:
        """Execute code in subprocess with security hardening."""
        fd, tmp_str = tempfile.mkstemp(suffix=".py", prefix="xlstruct_codegen_")
        tmp_path = PathLibPath(tmp_str)

        try:
            # ^ Write using the already-opened fd to avoid TOCTOU race
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(code)

            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(tmp_path),
                source_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_build_safe_env(),
                preexec_fn=_apply_resource_limits if sys.platform != "win32" else None,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except TimeoutError:
                proc.kill()
                await proc.communicate()
                return -1, "", f"Script killed after {timeout}s timeout."

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            return proc.returncode or 0, stdout, stderr

        finally:
            tmp_path.unlink(missing_ok=True)
