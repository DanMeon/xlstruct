"""Tests for the hardened DockerBackend (S2).

Config-shape tests always run (no daemon needed). Container probes run only when
Docker is available and use harmless probes: read the uid, attempt a write to the
read-only rootfs, and attempt a DNS-port connection that isolation should block.
"""

import asyncio
import json

import pytest

from xlstruct.codegen.backends.docker import DockerBackend, DockerConfig


def _docker_available() -> bool:
    try:
        import aiodocker
    except ImportError:
        return False

    async def _ping() -> bool:
        docker = aiodocker.Docker()
        try:
            await docker.version()
            return True
        finally:
            await docker.close()

    try:
        return asyncio.run(_ping())
    except Exception:
        return False


# * Always-on: the hardening must be present in the execution container config


class TestDockerExecConfigHardening:
    def test_exec_host_config_is_hardened(self):
        hc = DockerBackend()._exec_host_config().model_dump(exclude_none=True)
        assert hc["ReadonlyRootfs"] is True
        assert hc["CapDrop"] == ["ALL"]
        assert "/tmp" in hc["Tmpfs"]
        assert "no-new-privileges" in hc["SecurityOpt"]
        # ^ existing controls must be preserved
        assert hc["PidsLimit"] == 64
        assert hc["Memory"] > 0
        assert hc["MemorySwap"] == hc["Memory"]  # swap disabled
        # ^ no runtime override by default
        assert "Runtime" not in hc

    def test_exec_container_runs_as_nonroot_with_no_bytecode_writes(self):
        from xlstruct.codegen.backends.docker import _ContainerConfig, _HostConfig

        cfg = _ContainerConfig(
            Image="x",
            Cmd=["python"],
            User=DockerBackend()._cfg.user,
            Env=["PYTHONDONTWRITEBYTECODE=1"],
            HostConfig=_HostConfig(Memory=1, MemorySwap=1, CpuQuota=1),
        ).model_dump(exclude_none=True)
        assert cfg["User"] == "65534:65534"
        assert "PYTHONDONTWRITEBYTECODE=1" in cfg["Env"]

    def test_install_host_config_is_permissive(self):
        """The one-time package install must NOT be hardened (needs root + writes)."""
        hc = DockerBackend()._install_host_config().model_dump(exclude_none=True)
        assert hc["ReadonlyRootfs"] is False
        assert hc["CapDrop"] == []
        assert hc["Tmpfs"] == {}

    def test_gvisor_runtime_surfaces(self):
        hc = (
            DockerBackend(DockerConfig(runtime="runsc"))
            ._exec_host_config()
            .model_dump(exclude_none=True)
        )
        assert hc["Runtime"] == "runsc"

    def test_custom_seccomp_profile_surfaces(self, tmp_path):
        profile = tmp_path / "seccomp.json"
        profile.write_text(json.dumps({"defaultAction": "SCMP_ACT_ALLOW"}))
        hc = (
            DockerBackend(DockerConfig(seccomp_profile=profile))
            ._exec_host_config()
            .model_dump(exclude_none=True)
        )
        assert any(opt.startswith("seccomp=") for opt in hc["SecurityOpt"])

    def test_default_seccomp_is_not_disabled(self):
        """With no custom profile, Docker's built-in default profile stays active."""
        hc = DockerBackend()._exec_host_config().model_dump(exclude_none=True)
        assert not any("seccomp=unconfined" in opt for opt in hc["SecurityOpt"])


# * Gated: real container isolation probes


@pytest.fixture
def dummy_source(tmp_path):
    p = tmp_path / "source.xlsx"
    p.write_bytes(b"PK\x03\x04")  # ^ minimal bytes; probes do not read it
    return str(p)


@pytest.mark.skipif(not _docker_available(), reason="Docker daemon not available")
class TestDockerContainerProbes:
    async def test_runs_as_nonroot(self, dummy_source):
        backend = DockerBackend()
        code = "import os\nprint(os.getuid())"
        exit_code, stdout, stderr = await backend.execute(code, dummy_source, timeout=60)
        assert exit_code == 0, stderr
        assert stdout.strip().endswith("65534")

    async def test_root_filesystem_is_read_only(self, dummy_source):
        backend = DockerBackend()
        code = (
            "try:\n"
            "    open('/probe_root.txt', 'w').write('x')\n"
            "    print('WROTE')\n"
            "except OSError:\n"
            "    print('READONLY')\n"
        )
        exit_code, stdout, stderr = await backend.execute(code, dummy_source, timeout=60)
        assert exit_code == 0, stderr
        assert "READONLY" in stdout

    async def test_tmp_is_writable(self, dummy_source):
        backend = DockerBackend()
        code = "open('/tmp/probe.txt', 'w').write('ok')\nprint('TMP_OK')"
        exit_code, stdout, stderr = await backend.execute(code, dummy_source, timeout=60)
        assert exit_code == 0, stderr
        assert "TMP_OK" in stdout

    async def test_network_is_blocked(self, dummy_source):
        backend = DockerBackend()
        code = (
            "import socket\n"
            "try:\n"
            "    socket.create_connection(('8.8.8.8', 53), timeout=3)\n"
            "    print('NETWORK_OK')\n"
            "except OSError:\n"
            "    print('NETWORK_BLOCKED')\n"
        )
        exit_code, stdout, stderr = await backend.execute(code, dummy_source, timeout=60)
        assert exit_code == 0, stderr
        assert "NETWORK_BLOCKED" in stdout
