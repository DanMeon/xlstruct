"""Tests for codegen subpackage: executor, validation, schema_utils."""

import json
from unittest.mock import AsyncMock

from pydantic import BaseModel

from xlstruct.codegen.executor import (
    ALLOWED_ENV_KEYS,
    ALLOWED_IMPORTS,
    _build_safe_env,
    scan_blocked_imports,
)
from xlstruct.codegen.schema_utils import get_schema_source
from xlstruct.codegen.validation import ScriptValidator

# * Test schemas


class SampleRecord(BaseModel):
    name: str
    value: int
    note: str = ""  # ^ optional field


class InnerModel(BaseModel):
    label: str
    count: int


class OuterModel(BaseModel):
    title: str
    item: InnerModel


# * scan_blocked_imports (allowlist approach)


class TestScanBlockedImports:
    # * Allowed imports pass cleanly

    def test_clean_code_returns_empty(self):
        code = "import openpyxl\nimport json\nimport sys\n\nprint('hello')"
        assert scan_blocked_imports(code) == []

    def test_all_allowed_imports_pass(self):
        """Every module in the allowlist should pass."""
        for mod in ALLOWED_IMPORTS:
            code = f"import {mod}"
            assert scan_blocked_imports(code) == [], f"{mod} should be allowed"

    def test_from_import_allowed_submodule(self):
        code = "from datetime import date\nfrom collections import defaultdict"
        assert scan_blocked_imports(code) == []

    # * Disallowed imports detected

    def test_import_socket_detected(self):
        code = "import socket\nprint(socket.gethostname())"
        result = scan_blocked_imports(code)
        assert any("socket" in r for r in result)

    def test_from_http_client_detected(self):
        code = "from http.client import HTTPSConnection"
        result = scan_blocked_imports(code)
        assert any("http" in r for r in result)

    def test_import_subprocess_detected(self):
        code = "import subprocess\nsubprocess.run(['ls'])"
        result = scan_blocked_imports(code)
        assert any("subprocess" in r for r in result)

    def test_multiple_disallowed_imports_all_detected(self):
        code = "import socket\nimport subprocess\nfrom urllib.request import urlopen\n"
        result = scan_blocked_imports(code)
        assert any("socket" in r for r in result)
        assert any("subprocess" in r for r in result)
        assert any("urllib" in r for r in result)

    def test_import_os_detected(self):
        code = "import os\nprint(os.getcwd())"
        assert any("os" in r for r in scan_blocked_imports(code))

    def test_import_ctypes_detected(self):
        code = "import ctypes"
        assert any("ctypes" in r for r in scan_blocked_imports(code))

    def test_import_pickle_detected(self):
        code = "import pickle"
        assert any("pickle" in r for r in scan_blocked_imports(code))

    def test_import_importlib_detected(self):
        code = "import importlib"
        assert any("importlib" in r for r in scan_blocked_imports(code))

    def test_import_multiprocessing_detected(self):
        code = "import multiprocessing"
        assert any("multiprocessing" in r for r in scan_blocked_imports(code))

    def test_import_signal_detected(self):
        code = "import signal"
        assert any("signal" in r for r in scan_blocked_imports(code))

    def test_import_webbrowser_detected(self):
        code = "import webbrowser"
        assert any("webbrowser" in r for r in scan_blocked_imports(code))

    def test_import_xmlrpc_detected(self):
        code = "from xmlrpc.client import ServerProxy"
        result = scan_blocked_imports(code)
        assert any("xmlrpc" in r for r in result)

    def test_import_marshal_detected(self):
        code = "import marshal"
        assert any("marshal" in r for r in scan_blocked_imports(code))

    def test_import_shelve_detected(self):
        code = "import shelve"
        assert any("shelve" in r for r in scan_blocked_imports(code))

    def test_import_io_detected(self):
        """io module should not be in allowlist — can bypass open() block."""
        code = "import io\nio.open('/etc/passwd')"
        result = scan_blocked_imports(code)
        assert any("io" in r for r in result)

    def test_import_codecs_detected(self):
        code = "import codecs"
        result = scan_blocked_imports(code)
        assert any("codecs" in r for r in result)

    # * SyntaxError passes security scan (subprocess handles the error)

    def test_syntax_error_passes_security_scan(self):
        code = "def broken(\n    pass\n"
        result = scan_blocked_imports(code)
        assert result == []

    # * Builtin escape detection

    def test_dunder_import_detected(self):
        code = "__import__('os').system('whoami')"
        result = scan_blocked_imports(code)
        assert any("__import__" in r for r in result)

    def test_exec_detected(self):
        code = "exec('import os')"
        result = scan_blocked_imports(code)
        assert any("exec" in r for r in result)

    def test_eval_detected(self):
        code = "eval('1+1')"
        result = scan_blocked_imports(code)
        assert any("eval" in r for r in result)

    def test_open_detected(self):
        code = "data = open('/etc/passwd').read()"
        result = scan_blocked_imports(code)
        assert any("open" in r for r in result)

    # * Attribute access patterns

    def test_sys_modules_detected(self):
        code = "import sys\nos_mod = sys.modules['os']"
        result = scan_blocked_imports(code)
        assert any("sys.modules" in r for r in result)

    # * Dunder attribute escape detection

    def test_subclasses_detected(self):
        code = "().__class__.__bases__[0].__subclasses__()"
        result = scan_blocked_imports(code)
        assert any("__subclasses__" in r for r in result)
        assert any("__bases__" in r for r in result)

    def test_mro_detected(self):
        code = "type.__mro__"
        result = scan_blocked_imports(code)
        assert any("__mro__" in r for r in result)

    def test_globals_dunder_detected(self):
        code = "func.__globals__"
        result = scan_blocked_imports(code)
        assert any("__globals__" in r for r in result)

    def test_code_dunder_detected(self):
        code = "func.__code__"
        result = scan_blocked_imports(code)
        assert any("__code__" in r for r in result)

    def test_builtins_dunder_detected(self):
        code = "x.__builtins__"
        result = scan_blocked_imports(code)
        assert any("__builtins__" in r for r in result)


# * _build_safe_env


class TestBuildSafeEnv:
    def test_credential_key_stripped(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
        env = _build_safe_env()
        assert "OPENAI_API_KEY" not in env

    def test_aws_key_stripped(self, monkeypatch):
        monkeypatch.setenv("AWS_SECRET_KEY", "mysecret")
        env = _build_safe_env()
        assert "AWS_SECRET_KEY" not in env

    def test_arbitrary_env_var_stripped(self, monkeypatch):
        monkeypatch.setenv("MY_APP_CONFIG", "value123")
        env = _build_safe_env()
        # ^ Whitelist approach: non-allowed vars are stripped
        assert "MY_APP_CONFIG" not in env

    def test_allowed_key_preserved(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        env = _build_safe_env()
        assert env.get("PATH") == "/usr/bin:/bin"

    def test_all_allowed_keys_preserved(self, monkeypatch):
        for key in ALLOWED_ENV_KEYS:
            monkeypatch.setenv(key, f"test_{key}")
        env = _build_safe_env()
        for key in ALLOWED_ENV_KEYS:
            assert key in env

    def test_common_secrets_stripped(self, monkeypatch):
        secret_vars = {
            "OPENAI_API_KEY": "sk-secret",
            "ANTHROPIC_API_KEY": "sk-ant-secret",
            "AWS_ACCESS_KEY_ID": "aws_val",
            "GOOGLE_APPLICATION_CREDENTIALS": "gcp_val",
            "DATABASE_URL": "postgres://...",
            "GITHUB_TOKEN": "ghp_xxx",
        }
        for k, v in secret_vars.items():
            monkeypatch.setenv(k, v)
        env = _build_safe_env()
        for k in secret_vars:
            assert k not in env

    def test_returns_dict_of_strings(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        env = _build_safe_env()
        assert isinstance(env, dict)
        for k, v in env.items():
            assert isinstance(k, str)
            assert isinstance(v, str)


# * ScriptValidator._extract_traceback


class TestExtractTraceback:
    def test_short_stderr_returned_as_is(self):
        stderr = "Traceback (most recent call last):\n  File 'x.py', line 1\nValueError: bad"
        result = ScriptValidator._extract_traceback(stderr)
        assert "ValueError: bad" in result
        assert "[... truncated ...]" not in result

    def test_long_stderr_truncated_to_last_50_lines(self):
        lines = [f"line {i}" for i in range(200)]
        stderr = "\n".join(lines)
        result = ScriptValidator._extract_traceback(stderr)
        assert result.startswith("[... truncated ...]")
        # ^ Last line must be present
        assert "line 199" in result
        # ^ Lines from before the 50-line window must not appear
        assert "line 0" not in result

    def test_very_long_single_line_truncated(self):
        # ^ Exceeds max_chars=4000
        stderr = "E: " + "x" * 5000
        result = ScriptValidator._extract_traceback(stderr)
        assert result.startswith("[... truncated ...]")
        assert len(result) <= 4020  # ^ small slack for prefix


# * ScriptValidator._validate_output


class TestValidateOutput:
    def test_empty_stdout_returns_error(self):
        result = ScriptValidator._validate_output("", SampleRecord)
        assert "no output" in result.lower() or "empty stdout" in result.lower()

    def test_empty_stdout_with_row_count_mentions_rows(self):
        result = ScriptValidator._validate_output("", SampleRecord, total_data_rows=50)
        assert "50" in result

    def test_invalid_json_returns_error(self):
        result = ScriptValidator._validate_output("{not valid json", SampleRecord)
        assert "not valid JSON" in result or "JSONDecodeError" in result

    def test_non_list_json_returns_error(self):
        stdout = json.dumps({"name": "Alice", "value": 1})
        result = ScriptValidator._validate_output(stdout, SampleRecord)
        assert "array" in result.lower() or "list" in result.lower()

    def test_empty_list_returns_error(self):
        result = ScriptValidator._validate_output("[]", SampleRecord)
        assert "empty" in result.lower()

    def test_low_coverage_returns_error(self):
        # ^ 2 records out of 100 data rows = 2% coverage (below 10% threshold)
        data = [{"name": "a", "value": 1}, {"name": "b", "value": 2}]
        stdout = json.dumps(data)
        result = ScriptValidator._validate_output(stdout, SampleRecord, total_data_rows=100)
        assert "coverage" in result.lower() or "Low coverage" in result

    def test_valid_sample_returns_empty_string(self):
        data = [{"name": f"item_{i}", "value": i} for i in range(5)]
        stdout = json.dumps(data)
        result = ScriptValidator._validate_output(stdout, SampleRecord)
        assert result == ""

    def test_schema_validation_failure_returns_error(self):
        # ^ "value" field requires int, not a string
        data = [{"name": "Alice", "value": "not_an_int"}]
        stdout = json.dumps(data)
        result = ScriptValidator._validate_output(stdout, SampleRecord)
        assert "validation" in result.lower() or "VALIDATION ERROR" in result


# * ScriptValidator._filter_by_required_fields


class TestFilterByRequiredFields:
    def test_null_required_field_filtered_out(self):
        data = [
            {"name": "Alice", "value": 1},
            {"name": None, "value": 2},  # ^ name is required
        ]
        stdout = json.dumps(data)
        result_str = ScriptValidator._filter_by_required_fields(stdout, SampleRecord)
        result = json.loads(result_str)
        assert len(result) == 1
        assert result[0]["name"] == "Alice"

    def test_empty_string_required_field_filtered_out(self):
        data = [
            {"name": "Bob", "value": 10},
            {"name": "", "value": 20},  # ^ empty string treated as null
        ]
        stdout = json.dumps(data)
        result_str = ScriptValidator._filter_by_required_fields(stdout, SampleRecord)
        result = json.loads(result_str)
        assert len(result) == 1
        assert result[0]["name"] == "Bob"

    def test_all_valid_records_preserved(self):
        data = [{"name": f"item_{i}", "value": i} for i in range(5)]
        stdout = json.dumps(data)
        result_str = ScriptValidator._filter_by_required_fields(stdout, SampleRecord)
        result = json.loads(result_str)
        assert len(result) == 5

    def test_non_json_input_returned_as_is(self):
        bad_input = "this is not json at all"
        result = ScriptValidator._filter_by_required_fields(bad_input, SampleRecord)
        assert result == bad_input

    def test_schema_with_no_required_fields_no_filtering(self):
        class AllOptional(BaseModel):
            x: str = ""
            y: int = 0

        data = [{"x": "", "y": 0}, {"x": None, "y": None}]
        stdout = json.dumps(data)
        result_str = ScriptValidator._filter_by_required_fields(stdout, AllOptional)
        result = json.loads(result_str)
        # ^ No filtering because no required fields
        assert len(result) == 2


# * get_schema_source


class TestGetSchemaSource:
    def test_simple_model_returns_source(self):
        source = get_schema_source(SampleRecord)
        assert "SampleRecord" in source
        assert "name" in source

    def test_nested_model_includes_both_in_dependency_order(self):
        source = get_schema_source(OuterModel)
        # ^ Both models must be present
        assert "InnerModel" in source
        assert "OuterModel" in source
        # ^ Dependency (InnerModel) must appear before the dependent (OuterModel)
        assert source.index("InnerModel") < source.index("OuterModel")


# * ScriptValidator.validate (async)


class TestScriptValidatorValidate:
    async def test_disallowed_imports_returns_failure_without_execution(self):
        validator = ScriptValidator(timeout=10)
        code = "import socket\nprint(socket.gethostname())"
        result = await validator.validate(code, source_path="/fake/path.xlsx")
        assert result.success is False
        assert "socket" in result.truncated_traceback
        # ^ exit_code -1 signals pre-execution failure
        assert result.exit_code == -1

    async def test_successful_execution_returns_success(self):
        mock_backend = AsyncMock()
        valid_data = [{"name": "Alice", "value": 1}]
        mock_backend.execute.return_value = (0, json.dumps(valid_data), "")

        validator = ScriptValidator(timeout=10, backend=mock_backend)
        code = "import openpyxl\nprint('hello')"
        result = await validator.validate(
            code,
            source_path="/fake/path.xlsx",
            output_schema=SampleRecord,
        )
        assert result.success is True
        assert result.exit_code == 0

    async def test_execution_failure_returns_failure(self):
        mock_backend = AsyncMock()
        mock_backend.execute.return_value = (1, "", "Traceback: something broke")

        validator = ScriptValidator(timeout=10, backend=mock_backend)
        code = "import openpyxl\nraise RuntimeError('oops')"
        result = await validator.validate(code, source_path="/fake/path.xlsx")
        assert result.success is False
        assert result.exit_code == 1
        assert "something broke" in result.truncated_traceback

    async def test_timeout_returns_timed_out_result(self):
        mock_backend = AsyncMock()
        mock_backend.execute.return_value = (-1, "", "Script killed after 10s timeout.")

        validator = ScriptValidator(timeout=10, backend=mock_backend)
        # ^ Use only allowed imports so the security scan passes and execution reaches timeout
        code = "import openpyxl\nimport sys\nprint(sys.argv)"
        result = await validator.validate(code, source_path="/fake/path.xlsx")
        assert result.success is False
        assert result.timed_out is True
        assert result.exit_code == -1
