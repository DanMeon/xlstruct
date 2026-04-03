"""Security scanning for LLM-generated scripts.

AST-based static analysis to detect disallowed imports, dangerous builtins,
and sandbox escape attempts before script execution.

Execution backends have moved to xlstruct.codegen.backends.
Re-exports are provided for backward compatibility.
"""

import ast

# * Re-exports for backward compatibility
from xlstruct.codegen.backends.base import ExecutionBackend as ExecutionBackend  # noqa: F401
from xlstruct.codegen.backends.docker import DockerBackend as DockerBackend  # noqa: F401
from xlstruct.codegen.backends.docker import DockerConfig as DockerConfig  # noqa: F401
from xlstruct.codegen.backends.subprocess import ALLOWED_ENV_KEYS as ALLOWED_ENV_KEYS  # noqa: F401
from xlstruct.codegen.backends.subprocess import (
    SubprocessBackend as SubprocessBackend,  # noqa: F401
)
from xlstruct.codegen.backends.subprocess import (
    _build_safe_env as _build_safe_env,  # noqa: F401  # pyright: ignore[reportPrivateUsage]
)

# * Security constants

# ^ Allowlist: only these top-level modules may be imported in generated scripts
ALLOWED_IMPORTS = frozenset(
    {
        # * Excel processing
        "openpyxl",
        "python_calamine",
        # * Data modeling
        "pydantic",
        # * Standard library — data & math
        "json",
        "csv",
        "re",
        "datetime",
        "decimal",
        "math",
        "statistics",
        "numbers",
        "fractions",
        # * Standard library — typing & structures
        "typing",
        "typing_extensions",
        "enum",
        "collections",
        "dataclasses",
        "abc",
        "types",
        # * Standard library — utilities
        "copy",
        "itertools",
        "functools",
        "string",
        "sys",  # ^ needed for sys.argv
        "warnings",
        "textwrap",
        "unicodedata",
    }
)

# ^ Builtin calls that are never allowed in generated scripts
BLOCKED_BUILTINS = frozenset(
    {
        "__import__",
        "exec",
        "eval",
        "compile",
        "open",
        "breakpoint",
    }
)

# ^ Dunder attributes that indicate sandbox escape attempts
BLOCKED_DUNDER_ATTRS = frozenset(
    {
        "__import__",
        "__subclasses__",
        "__bases__",
        "__mro__",
        "__globals__",
        "__code__",
        "__builtins__",
    }
)

# ^ Specific dotted access patterns blocked
BLOCKED_ATTR_PATTERNS = frozenset(
    {
        "sys.modules",
        "sys.path",
        "sys._getframe",
        "sys.meta_path",
    }
)

# ^ Builtin functions that can access blocked dunder attrs via string argument
_ATTR_ACCESS_BUILTINS = frozenset({"getattr", "setattr", "delattr"})


# * Security scanning


def scan_blocked_imports(code: str) -> list[str]:
    """Scan generated code for disallowed imports, dangerous builtins, and escape attempts.

    Uses an allowlist approach — only explicitly permitted modules may be imported.

    Checks:
    1. Import statements against the ALLOWED_IMPORTS allowlist
    2. Calls to dangerous builtins (__import__, exec, eval, compile, open, breakpoint)
    3. Attribute access patterns that indicate sandbox escape (sys.modules, etc.)
    4. Dunder attribute access (__subclasses__, __globals__, __builtins__, etc.)

    Returns list of violation descriptions, empty if clean.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # ^ Syntax errors are not security violations — let subprocess handle them
        # so the LLM self-correction loop can see the actual SyntaxError traceback
        return []

    violations: list[str] = []

    for node in ast.walk(tree):
        # * Check import statements against allowlist
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_module = alias.name.split(".")[0]
                if root_module not in ALLOWED_IMPORTS:
                    violations.append(f"disallowed import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root_module = node.module.split(".")[0]
                if root_module not in ALLOWED_IMPORTS:
                    violations.append(f"disallowed import: {node.module}")

        # * Check calls to dangerous builtins
        elif isinstance(node, ast.Call):
            func_name = _get_call_name(node)
            if func_name in BLOCKED_BUILTINS:
                violations.append(f"blocked builtin call: {func_name}")
            # ^ Detect getattr/setattr/delattr with blocked dunder string args
            if func_name in _ATTR_ACCESS_BUILTINS and len(node.args) >= 2:
                arg = node.args[1]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if arg.value in BLOCKED_DUNDER_ATTRS:
                        violations.append(f"blocked dunder via {func_name}(): {arg.value}")

        # * Check dangerous attribute access
        elif isinstance(node, ast.Attribute):
            # ^ Specific dotted patterns (sys.modules, etc.)
            attr_chain = _get_attr_chain(node)
            if attr_chain in BLOCKED_ATTR_PATTERNS:
                violations.append(f"blocked attribute access: {attr_chain}")
            # ^ Dunder attributes that enable sandbox escape
            if node.attr in BLOCKED_DUNDER_ATTRS:
                violations.append(f"blocked dunder attribute: {node.attr}")

    return violations


def _get_call_name(node: ast.Call) -> str:
    """Extract the function name from a Call node.

    Only returns names for direct calls (ast.Name), not attribute calls
    (ast.Attribute) — e.g. wb.open() should NOT be flagged as 'open'.
    """
    if isinstance(node.func, ast.Name):
        return node.func.id
    return ""


def _get_attr_chain(node: ast.Attribute) -> str:
    """Build dotted attribute chain (e.g., 'sys.modules') from an Attribute node."""
    parts: list[str] = [node.attr]
    current = node.value
    # ^ Walk up to 3 levels to catch patterns like __builtins__.__import__
    for _ in range(3):
        if isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        elif isinstance(current, ast.Name):
            parts.append(current.id)
            break
        else:
            break
    return ".".join(reversed(parts))
