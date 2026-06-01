"""Tests for execution backend resolution (S1): sandbox-by-default, fail-closed."""

import pytest

from xlstruct.codegen.backends import resolver as resolver_mod
from xlstruct.codegen.backends.docker import DockerBackend
from xlstruct.codegen.backends.resolver import resolve_execution_backend
from xlstruct.codegen.backends.subprocess import SubprocessBackend
from xlstruct.config import ExtractorConfig
from xlstruct.exceptions import CodegenSecurityError, ErrorCode


class TestResolveExecutionBackend:
    def test_explicit_backend_always_wins(self):
        """An injected backend is returned verbatim regardless of sandbox mode."""
        explicit = SubprocessBackend(trusted=True)
        for mode in ("auto", "docker", "subprocess"):
            assert resolve_execution_backend(explicit, mode) is explicit

    def test_subprocess_mode_returns_trusted_subprocess(self):
        backend = resolve_execution_backend(None, "subprocess")
        assert isinstance(backend, SubprocessBackend)
        assert backend._trusted is True  # ^ explicit opt-in suppresses the warning

    def test_docker_mode_returns_docker_backend(self):
        # ^ DockerBackend construction is lazy; no daemon needed here
        backend = resolve_execution_backend(None, "docker")
        assert isinstance(backend, DockerBackend)

    def test_auto_uses_docker_when_available(self, monkeypatch):
        monkeypatch.setattr(resolver_mod, "docker_available", lambda: True)
        backend = resolve_execution_backend(None, "auto")
        assert isinstance(backend, DockerBackend)

    def test_auto_fails_closed_without_docker(self, monkeypatch):
        """The core S1 guarantee: no silent fallback to the non-isolating subprocess."""
        monkeypatch.setattr(resolver_mod, "docker_available", lambda: False)
        with pytest.raises(CodegenSecurityError) as exc_info:
            resolve_execution_backend(None, "auto")
        assert exc_info.value.code == ErrorCode.CODEGEN_NO_SANDBOX
        # ^ Error must point the user at both remedies
        msg = str(exc_info.value)
        assert "xlstruct[docker]" in msg
        assert "subprocess" in msg


class TestConfigDefault:
    def test_codegen_security_error_is_public(self):
        # ^ new public exception must be catchable from the top-level package
        import xlstruct

        assert xlstruct.CodegenSecurityError is CodegenSecurityError

    def test_codegen_sandbox_defaults_to_auto(self):
        assert ExtractorConfig().codegen_sandbox == "auto"

    def test_codegen_sandbox_rejects_unknown_value(self):
        with pytest.raises(ValueError):
            ExtractorConfig(codegen_sandbox="yolo")  # type: ignore[arg-type]
