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

# ^ Default UID:GID for the execution container (nobody:nogroup)
_DEFAULT_NONROOT_USER = "65534:65534"


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
    # * Hardening — applied to the execution container, never to the one-time install step.
    user: str = Field(
        default=_DEFAULT_NONROOT_USER,
        description="UID:GID for the execution container. Defaults to nobody. "
        "Empty string uses the image default (root) — not recommended.",
    )
    read_only_rootfs: bool = Field(
        default=True,
        description="Mount the container root filesystem read-only (a /tmp tmpfs stays writable).",
    )
    cap_drop: list[str] = Field(
        default_factory=lambda: ["ALL"],
        description="Linux capabilities to drop in the execution container.",
    )
    tmpfs_size: str = Field(
        default="64m",
        description="Size of the writable /tmp tmpfs mounted when the rootfs is read-only.",
    )
    runtime: str | None = Field(
        default=None,
        description="Container runtime for the execution step, e.g. 'runsc' for gVisor. "
        "None uses Docker's default runtime.",
    )
    seccomp_profile: PathLibPath | None = Field(
        default=None,
        description="Path to a custom seccomp JSON profile applied to the execution "
        "container. None keeps Docker's built-in default seccomp profile active.",
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
    CapDrop: list[str] = Field(
        default_factory=list,
        description="Linux capabilities to drop (e.g. ['ALL'])",
    )
    Tmpfs: dict[str, str] = Field(
        default_factory=dict,
        description="Writable tmpfs mounts (path -> mount options)",
    )
    SecurityOpt: list[str] = Field(
        default_factory=lambda: ["no-new-privileges"],
        description="Security options (e.g. no-new-privileges, seccomp=...)",
    )
    Runtime: str | None = Field(
        default=None,
        description="Container runtime (e.g. 'runsc' for gVisor); None = Docker default",
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
    User: str = Field(default="", description="UID:GID to run as; empty = image default (root)")
    Env: list[str] = Field(default_factory=list, description="Environment as KEY=VALUE strings")
    HostConfig: _HostConfig = Field(description="Host-level resource constraints")


class DockerBackend:
    """Execute scripts in an isolated Docker container via aiodocker.

    Provides full OS-level sandboxing for untrusted codegen output: no host
    filesystem access, no network, a non-root user, all capabilities dropped, a
    read-only root filesystem (with a small writable ``/tmp`` tmpfs), restricted
    memory/CPU/PIDs, no-new-privileges, and Docker's seccomp profile (or a custom
    one). Requires the Docker daemon and the ``aiodocker`` package
    (install with ``pip install xlstruct[docker]``).
    """

    def __init__(self, config: DockerConfig | None = None) -> None:
        cfg = config or DockerConfig()
        self._cfg = cfg
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

    def _install_host_config(self) -> _HostConfig:
        """Permissive host config for the one-time package install (needs root + writes)."""
        mem = _parse_mem_limit(self._mem_limit)
        return _HostConfig(Memory=mem, MemorySwap=mem, CpuQuota=self._cpu_quota)

    def _exec_host_config(self) -> _HostConfig:
        """Hardened host config for running untrusted scripts."""
        mem = _parse_mem_limit(self._mem_limit)
        security_opt = ["no-new-privileges"]
        if self._cfg.seccomp_profile is not None:
            # ^ aiodocker passes profiles inline as JSON content, not a path.
            profile = self._cfg.seccomp_profile.read_text(encoding="utf-8")
            security_opt.append(f"seccomp={profile}")

        tmpfs: dict[str, str] = {}
        if self._cfg.read_only_rootfs:
            # ^ Scripts only print to stdout; a small writable /tmp covers any scratch.
            tmpfs["/tmp"] = f"rw,nosuid,nodev,noexec,size={self._cfg.tmpfs_size},mode=1777"

        return _HostConfig(
            Memory=mem,
            MemorySwap=mem,
            CpuQuota=self._cpu_quota,
            ReadonlyRootfs=self._cfg.read_only_rootfs,
            CapDrop=list(self._cfg.cap_drop),
            Tmpfs=tmpfs,
            SecurityOpt=security_opt,
            Runtime=self._cfg.runtime,
        )

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

            # * Stage 1: Install packages with network enabled → commit (permissive: needs root)
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
                HostConfig=self._install_host_config(),
            )

            container = await docker.containers.create(
                config=install_config.model_dump(exclude_none=True),
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

        Uses the prepared image (packages pre-installed) with network disabled,
        a non-root user, dropped capabilities, and a read-only root filesystem.
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

        # * Stage 2: Run script under the hardened, network-disabled config
        async with aiodocker.Docker() as docker:
            container_config = _ContainerConfig(
                Image=self._ready_image,
                Cmd=[
                    "python",
                    "/workspace/script.py",
                    f"/workspace/{source.name}",
                ],
                NetworkDisabled=self._network_disabled,
                User=self._cfg.user,
                # ^ Avoid .pyc writes against the read-only rootfs; flush stdout promptly.
                Env=["PYTHONDONTWRITEBYTECODE=1", "PYTHONUNBUFFERED=1"],
                HostConfig=self._exec_host_config(),
            )

            container = await docker.containers.create(
                config=container_config.model_dump(exclude_none=True),
            )

            try:
                # * Copy files into container via tar archive (before start: lands in the
                #   writable layer, then stays readable once the rootfs is mounted read-only)
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
