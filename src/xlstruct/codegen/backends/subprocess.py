"""SubprocessBackend — hardened subprocess execution (trusted/dev-only).

This backend is NOT a security boundary. The pre-execution AST scan is bypassable
by a code generator, and a subprocess shares the host filesystem, so a hostile
script can still read on-disk secrets. Use ``DockerBackend`` for untrusted output;
keep this backend only for trusted/development environments. The measures below are
defense-in-depth (reduce env/import surface, bound resources, contain timeouts).
"""

import asyncio
import functools
import logging
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path as PathLibPath

logger = logging.getLogger(__name__)

# ^ Shipped helper that runs the script with a reduced builtins namespace.
_BOOTSTRAP_PATH = PathLibPath(__file__).parent / "_subprocess_bootstrap.py"

# ^ Emit the "not a security boundary" notice once per process, not per script.
_trusted_warning_emitted = False

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


def _apply_resource_limits(timeout: int) -> None:
    """Apply resource limits in the forked child before exec (POSIX only).

    Runs as ``preexec_fn``. Critical limits (CPU, file descriptors, file size)
    are fail-closed: if they cannot be set the exception propagates, the child
    never execs, and the parent's spawn fails. Address-space and process-count
    limits are best-effort because their failure is environmental (e.g. an
    unstable ``RLIMIT_AS`` on macOS, or a host already near its per-UID process
    cap), not a missing boundary — CPU time plus process-group SIGKILL still
    bound runaway and fork-bomb damage.
    """
    import resource

    cpu = max(1, timeout)
    # * Fail-closed limits — failure here aborts the child exec.
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 5))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    resource.setrlimit(resource.RLIMIT_FSIZE, (50 * 1024**2, 50 * 1024**2))

    # * Best-effort limits — environmental failure must not break trusted runs.
    #   Logging is unsafe post-fork, so failures are swallowed silently here.
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (512, 512))  # ^ fork-bomb cap
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))  # ^ 512MB
    except (ValueError, OSError):
        pass


class SubprocessBackend:
    """Execute scripts in a hardened subprocess (trusted/dev-only — NOT a boundary).

    Defense-in-depth measures:
    - Credential environment variables stripped (whitelist approach)
    - Isolated interpreter (``python -I``): host env vars, user site-packages, and
      the script directory are excluded from the import path
    - Reduced builtins via bootstrap (eval/exec/compile/breakpoint/input removed)
    - Resource limits: CPU time, file descriptors, file size (fail-closed); memory
      and process count (best-effort)
    - Timeout kills the whole process group (orphan-safe) with a bounded drain
    """

    def __init__(self, trusted: bool = False) -> None:
        """Create the subprocess backend.

        Args:
            trusted: Acknowledge that this backend is NOT a security boundary and
                is being used in a trusted/development environment. When False, a
                one-time warning is emitted on first use. Set True to suppress it.
        """
        self._trusted = trusted

    def _warn_if_untrusted(self) -> None:
        global _trusted_warning_emitted
        if self._trusted or _trusted_warning_emitted:
            return
        _trusted_warning_emitted = True
        logger.warning(
            "SubprocessBackend is NOT a security boundary — the AST scan is bypassable "
            "and the script shares the host filesystem. Use DockerBackend for untrusted "
            "codegen output, or pass SubprocessBackend(trusted=True) to acknowledge "
            "trusted/dev-only use and silence this warning."
        )

    async def execute(
        self,
        code: str,
        source_path: str,
        timeout: int,
    ) -> tuple[int, str, str]:
        """Execute code in a hardened subprocess.

        Raises:
            RuntimeError: Fail-closed — the subprocess could not be spawned with the
                required resource limits applied.
        """
        self._warn_if_untrusted()

        fd, tmp_str = tempfile.mkstemp(suffix=".py", prefix="xlstruct_codegen_")
        tmp_path = PathLibPath(tmp_str)

        try:
            # ^ Write using the already-opened fd to avoid TOCTOU race
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(code)

            is_posix = sys.platform != "win32"
            if is_posix:
                # ^ preexec applies limits (fail-closed); new session enables killpg
                preexec = functools.partial(_apply_resource_limits, timeout)
            else:
                preexec = None
                logger.warning(
                    "Resource limits are not enforced on Windows for SubprocessBackend "
                    "(preexec_fn unsupported); rely on DockerBackend for real isolation."
                )

            try:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-I",  # ^ isolated: ignore env vars, user site, and script dir
                    str(_BOOTSTRAP_PATH),
                    str(tmp_path),
                    source_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=_build_safe_env(),
                    preexec_fn=preexec,
                    start_new_session=is_posix,
                )
            except (OSError, ValueError, subprocess.SubprocessError) as e:
                # ^ Fail-closed: a preexec failure surfaces as subprocess.SubprocessError
                #   ("Exception occurred in preexec_fn."); spawn errors as OSError/ValueError.
                #   Either way the limits were not applied — refuse to run.
                raise RuntimeError(
                    f"Refusing to execute codegen script: subprocess hardening could not "
                    f"be applied ({e})."
                ) from e

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except TimeoutError:
                await self._terminate(proc, is_posix)
                return -1, "", f"Script killed after {timeout}s timeout."

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            return proc.returncode or 0, stdout, stderr

        finally:
            tmp_path.unlink(missing_ok=True)

    @staticmethod
    async def _terminate(proc: "asyncio.subprocess.Process", is_posix: bool) -> None:
        """Kill a timed-out process (and its group on POSIX) with a bounded drain."""
        try:
            if is_posix:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError):
            pass
        # ^ Bounded drain — a wedged pipe must not hang the caller forever.
        try:
            await asyncio.wait_for(proc.communicate(), timeout=5)
        except (TimeoutError, ProcessLookupError):
            pass
