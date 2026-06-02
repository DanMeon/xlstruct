"""Resolve the execution backend for codegen — sandbox-by-default, fail-closed."""

import importlib.util
import logging
from typing import Literal

from xlstruct.codegen.backends.base import ExecutionBackend
from xlstruct.codegen.backends.docker import DockerBackend
from xlstruct.codegen.backends.subprocess import SubprocessBackend
from xlstruct.exceptions import CodegenSecurityError, ErrorCode

logger = logging.getLogger(__name__)

CodegenSandbox = Literal["auto", "docker", "subprocess"]


def docker_available() -> bool:
    """True if ``aiodocker`` is importable. Does not probe the Docker daemon.

    Importability is treated as the signal that the user installed
    ``xlstruct[docker]`` intending to sandbox execution. If the daemon is down,
    DockerBackend fails clearly at execution time (still fail-closed).
    """
    return importlib.util.find_spec("aiodocker") is not None


def resolve_execution_backend(
    explicit: ExecutionBackend | None,
    sandbox: CodegenSandbox,
) -> ExecutionBackend:
    """Pick the backend for executing untrusted, LLM-generated codegen output.

    Precedence: an explicitly injected backend always wins. Otherwise ``sandbox``
    decides:

    - ``"auto"``: DockerBackend when ``aiodocker`` is installed, else fail-closed
      with a :class:`CodegenSecurityError` (never a silent fallback to subprocess).
    - ``"docker"``: DockerBackend (errors at execution time if the daemon/package
      is absent).
    - ``"subprocess"``: the trusted/dev-only SubprocessBackend, which is NOT a
      security boundary.
    """
    if explicit is not None:
        return explicit

    if sandbox == "subprocess":
        logger.warning(
            "codegen_sandbox='subprocess' selected — executing untrusted LLM output in a "
            "non-isolating subprocess. Use only in trusted/development environments."
        )
        return SubprocessBackend(trusted=True)

    if sandbox == "docker":
        return DockerBackend()

    # * auto — sandbox when possible, fail-closed otherwise
    if docker_available():
        return DockerBackend()

    raise CodegenSecurityError(
        "Codegen executes untrusted, LLM-generated Python, and no OS-level sandbox is "
        'available. Install Docker support (`pip install "xlstruct[docker]"` with a running '
        "Docker daemon) to sandbox execution, or — only in a trusted/development environment "
        "— opt into the unsandboxed subprocess backend via "
        'ExtractorConfig(codegen_sandbox="subprocess") or '
        "execution_backend=SubprocessBackend(trusted=True). The subprocess backend is NOT a "
        "security boundary.",
        code=ErrorCode.CODEGEN_NO_SANDBOX,
    )
