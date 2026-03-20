"""DockerBackend — full OS-level isolation via Docker containers."""

import asyncio
import io
import logging
import tarfile
from pathlib import Path as PathLibPath

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ^ pip packages installed during image preparation
DOCKER_PIP_PACKAGES = ("openpyxl", "python-calamine")

# ^ Suffix appended to base image name for the prepared image
_PREPARED_IMAGE_TAG = "xlstruct-ready"


class DockerConfig(BaseModel):
    """Docker backend configuration for codegen execution."""

    image: str = Field(
        default="python:3.11-slim",
        description="Docker image. Must have Python installed.",
    )
    mem_limit: str = Field(
        default="512m",
        description="Container memory limit (e.g. '512m', '1g').",
    )
    cpu_quota: int = Field(
        default=100_000,
        description="CPU quota in microseconds per 100ms period (100_000 = 1 core).",
    )
    network_disabled: bool = Field(
        default=True,
        description="Disable network access for isolation.",
    )
    auto_pull: bool = Field(
        default=True,
        description="Pull the Docker image if not found locally.",
    )


class _HostConfig(BaseModel):
    """Docker container host-level resource constraints."""

    Memory: int = Field(description="Memory limit in bytes")
    MemorySwap: int = Field(
        description="Total memory + swap limit in bytes (same as Memory to disable swap)",
    )
    CpuQuota: int = Field(description="CPU quota in microseconds per CpuPeriod")
    CpuPeriod: int = Field(default=100_000, description="CPU CFS period in microseconds")
    PidsLimit: int = Field(default=64, description="Max number of PIDs in the container")
    ReadonlyRootfs: bool = Field(default=False, description="Mount root filesystem as read-only")
    SecurityOpt: list[str] = Field(
        default_factory=lambda: ["no-new-privileges"],
        description="Security options (e.g. no-new-privileges)",
    )


class _ContainerConfig(BaseModel):
    """Docker container creation config (maps to aiodocker create API)."""

    Image: str = Field(description="Docker image name")
    Cmd: list[str] = Field(description="Command to execute in the container")
    WorkingDir: str = Field(
        default="/workspace",
        description="Working directory inside the container",
    )
    NetworkDisabled: bool = Field(default=True, description="Disable network access for isolation")
    HostConfig: _HostConfig = Field(description="Host-level resource constraints")


class DockerBackend:
    """Execute scripts in an isolated Docker container via aiodocker.

    Provides full OS-level sandboxing: no host filesystem access, no network,
    restricted memory/CPU. Requires Docker daemon and the ``aiodocker`` package
    (install with ``pip install xlstruct[docker]``).
    """

    def __init__(self, config: DockerConfig | None = None) -> None:
        cfg = config or DockerConfig()
        self._image = cfg.image
        self._mem_limit = cfg.mem_limit
        self._cpu_quota = cfg.cpu_quota
        self._network_disabled = cfg.network_disabled
        self._auto_pull = cfg.auto_pull
        self._ready_image: str | None = None

    @property
    def _prepared_image_name(self) -> str:
        """Tag name for the prepared image with pre-installed packages."""
        # ^ e.g. "python:3.11-slim" → "python:3.11-slim-xlstruct-ready"
        return f"{self._image}-{_PREPARED_IMAGE_TAG}"

    async def _ensure_image(self) -> None:
        """Prepare a Docker image with dependencies pre-installed.

        Stage 1 (once): Pull base image → run pip install with network → commit as prepared image.
        Subsequent calls: Skip if prepared image already exists.
        """
        if self._ready_image:
            return

        try:
            import aiodocker
        except ImportError:
            raise ImportError(
                "aiodocker is required for DockerBackend. "
                "Install it with: pip install xlstruct[docker]"
            ) from None

        prepared_name = self._prepared_image_name

        async with aiodocker.Docker() as docker:
            # ^ Check if prepared image already exists
            try:
                await docker.images.inspect(prepared_name)
                self._ready_image = prepared_name
                logger.info("Using prepared image: %s", prepared_name)
                return
            except aiodocker.exceptions.DockerError:
                pass

            # ^ Ensure base image exists
            try:
                await docker.images.inspect(self._image)
            except aiodocker.exceptions.DockerError:
                if not self._auto_pull:
                    raise
                logger.info("Pulling base image: %s", self._image)
                await docker.pull(self._image)

            # * Stage 1: Install packages with network enabled → commit
            logger.info("Preparing image: installing %s", ", ".join(DOCKER_PIP_PACKAGES))
            install_config = _ContainerConfig(
                Image=self._image,
                Cmd=[
                    "pip",
                    "install",
                    "-q",
                    *DOCKER_PIP_PACKAGES,
                ],
                NetworkDisabled=False,
                HostConfig=_HostConfig(
                    Memory=_parse_mem_limit(self._mem_limit),
                    MemorySwap=_parse_mem_limit(self._mem_limit),
                    CpuQuota=self._cpu_quota,
                ),
            )

            container = await docker.containers.create(
                config=install_config.model_dump(),
            )

            try:
                await container.start()
                exit_info = await asyncio.wait_for(container.wait(), timeout=300)

                if exit_info["StatusCode"] != 0:
                    logs = await container.log(stderr=True)
                    raise RuntimeError(
                        f"Failed to prepare Docker image (exit {exit_info['StatusCode']}): "
                        f"{''.join(logs)[:500]}"
                    )

                # ^ Commit the container as a new image
                await container.commit(repository=prepared_name)
                logger.info("Prepared image committed: %s", prepared_name)
                self._ready_image = prepared_name
            finally:
                try:
                    await container.delete(force=True)
                except aiodocker.exceptions.DockerError:
                    pass

    async def execute(
        self,
        code: str,
        source_path: str,
        timeout: int,
    ) -> tuple[int, str, str]:
        """Execute code in a Docker container with full isolation.

        Uses the prepared image (packages pre-installed) with network disabled.
        """
        try:
            import aiodocker
        except ImportError:
            raise ImportError(
                "aiodocker is required for DockerBackend. "
                "Install it with: pip install xlstruct[docker]"
            ) from None

        await self._ensure_image()
        assert self._ready_image is not None

        source = PathLibPath(source_path)
        if not source.exists():
            return 1, "", f"Source file not found: {source_path}"

        # * Stage 2: Run script with network disabled
        async with aiodocker.Docker() as docker:
            container_config = _ContainerConfig(
                Image=self._ready_image,
                Cmd=[
                    "python",
                    "/workspace/script.py",
                    f"/workspace/{source.name}",
                ],
                NetworkDisabled=self._network_disabled,
                HostConfig=_HostConfig(
                    Memory=_parse_mem_limit(self._mem_limit),
                    MemorySwap=_parse_mem_limit(self._mem_limit),
                    CpuQuota=self._cpu_quota,
                ),
            )

            container = await docker.containers.create(
                config=container_config.model_dump(),
            )

            try:
                # * Copy files into container via tar archive
                tar_bytes = _build_tar_archive(
                    ("script.py", code.encode("utf-8")),
                    (source.name, source.read_bytes()),
                )
                await container.put_archive("/workspace", tar_bytes)

                # * Start and wait
                await container.start()

                try:
                    exit_info = await asyncio.wait_for(
                        container.wait(),
                        timeout=timeout,
                    )
                    exit_code: int = exit_info["StatusCode"]
                except TimeoutError:
                    await container.kill()
                    return -1, "", f"Script killed after {timeout}s timeout."

                # * Collect logs
                stdout_logs = await container.log(stdout=True)
                stderr_logs = await container.log(stderr=True)

                stdout = "".join(stdout_logs)
                stderr = "".join(stderr_logs)

                return exit_code, stdout, stderr

            finally:
                try:
                    await container.delete(force=True)
                except aiodocker.exceptions.DockerError:
                    pass


def _parse_mem_limit(limit: str) -> int:
    """Parse Docker memory limit string (e.g. '512m') to bytes."""
    limit = limit.strip().lower()
    multipliers = {"k": 1024, "m": 1024**2, "g": 1024**3}
    if limit[-1] in multipliers:
        return int(limit[:-1]) * multipliers[limit[-1]]
    return int(limit)


def _build_tar_archive(*files: tuple[str, bytes]) -> bytes:
    """Build an in-memory tar archive from (name, content) pairs."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, content in files:
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    buf.seek(0)
    return buf.read()
