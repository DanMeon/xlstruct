"""ExecutionBackend protocol — interface for all script execution backends."""

from typing import Protocol


class ExecutionBackend(Protocol):
    """Protocol for script execution backends.

    Implementations:
    - SubprocessBackend: Hardened subprocess (default).
    - DockerBackend: Full OS-level isolation via Docker.
    """

    async def execute(
        self,
        code: str,
        source_path: str,
        timeout: int,
    ) -> tuple[int, str, str]:
        """Execute a Python script and return results.

        Args:
            code: Python script source code.
            source_path: Path to the Excel file (passed as CLI arg).
            timeout: Maximum execution time in seconds.

        Returns:
            Tuple of (exit_code, stdout, stderr).
            exit_code -1 indicates timeout.
        """
        ...
