"""Tests for the hardened (trusted/dev-only) SubprocessBackend (S1).

All probes are harmless: they read an env var the test sets, check which builtins
are present, print argv, or sleep. None perform destructive, credential-reading, or
exfiltrating actions.
"""

import logging
import sys

import pytest

from xlstruct.codegen.backends import subprocess as subprocess_mod
from xlstruct.codegen.backends.subprocess import SubprocessBackend

# ^ preexec_fn / killpg / setrlimit are POSIX-only; the hardening behaves differently
#   on Windows (warns, no limits), so these execution tests target POSIX.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="subprocess hardening probes are POSIX-only"
)


@pytest.fixture
def source(tmp_path):
    """A throwaway source path passed to scripts as argv[1]."""
    p = tmp_path / "source.xlsx"
    p.write_bytes(b"")
    return str(p)


class TestSubprocessHardening:
    async def test_legitimate_script_runs(self, source):
        backend = SubprocessBackend(trusted=True)
        code = "import openpyxl\nprint(len([1, 2, 3]))"
        exit_code, stdout, stderr = await backend.execute(code, source, timeout=10)
        assert exit_code == 0, stderr
        assert "3" in stdout

    async def test_credential_env_is_stripped(self, source, monkeypatch):
        """A secret in the parent env must not reach the child (whitelist)."""
        monkeypatch.setenv("XLSTRUCT_PROBE_SECRET", "topsecret")
        backend = SubprocessBackend(trusted=True)
        code = "import os\nprint(os.environ.get('XLSTRUCT_PROBE_SECRET', 'ABSENT'))"
        exit_code, stdout, stderr = await backend.execute(code, source, timeout=10)
        assert exit_code == 0, stderr
        assert "ABSENT" in stdout
        assert "topsecret" not in stdout

    async def test_dynamic_exec_builtins_are_stripped(self, source):
        """The bootstrap removes eval/exec/compile/breakpoint/input from builtins."""
        backend = SubprocessBackend(trusted=True)
        code = (
            "for name in ('eval', 'exec', 'compile', 'breakpoint', 'input'):\n"
            "    try:\n"
            "        __builtins__[name] if isinstance(__builtins__, dict) "
            "else getattr(__builtins__, name)\n"
            "        print(name, 'PRESENT')\n"
            "    except (KeyError, AttributeError):\n"
            "        print(name, 'STRIPPED')\n"
        )
        exit_code, stdout, stderr = await backend.execute(code, source, timeout=10)
        assert exit_code == 0, stderr
        for name in ("eval", "exec", "compile", "breakpoint", "input"):
            assert f"{name} STRIPPED" in stdout

    async def test_source_path_passed_as_argv(self, source):
        backend = SubprocessBackend(trusted=True)
        code = "import sys\nprint(sys.argv[1])"
        exit_code, stdout, stderr = await backend.execute(code, source, timeout=10)
        assert exit_code == 0, stderr
        assert source in stdout

    async def test_timeout_is_contained(self, source):
        """A sleeping script is killed (process group) and returns promptly."""
        backend = SubprocessBackend(trusted=True)
        code = "import time\ntime.sleep(30)"
        exit_code, stdout, stderr = await backend.execute(code, source, timeout=1)
        assert exit_code == -1
        assert "timeout" in stderr.lower()

    async def test_fail_closed_when_limits_cannot_be_applied(self, source, monkeypatch):
        """If resource limits cannot be applied, refuse to run (no unsandboxed exec)."""

        def _boom(_timeout):
            raise ValueError("simulated setrlimit failure")

        monkeypatch.setattr(subprocess_mod, "_apply_resource_limits", _boom)
        backend = SubprocessBackend(trusted=True)
        code = "print('should not run')"
        with pytest.raises(RuntimeError, match="hardening could not be applied"):
            await backend.execute(code, source, timeout=10)


class TestTrustedWarning:
    def test_untrusted_backend_warns_once(self, monkeypatch, caplog):
        monkeypatch.setattr(subprocess_mod, "_trusted_warning_emitted", False)
        backend = SubprocessBackend()
        with caplog.at_level(logging.WARNING):
            backend._warn_if_untrusted()
            backend._warn_if_untrusted()
        hits = [r for r in caplog.records if "NOT a security boundary" in r.getMessage()]
        assert len(hits) == 1

    def test_trusted_backend_does_not_warn(self, monkeypatch, caplog):
        monkeypatch.setattr(subprocess_mod, "_trusted_warning_emitted", False)
        backend = SubprocessBackend(trusted=True)
        with caplog.at_level(logging.WARNING):
            backend._warn_if_untrusted()
        hits = [r for r in caplog.records if "NOT a security boundary" in r.getMessage()]
        assert hits == []
