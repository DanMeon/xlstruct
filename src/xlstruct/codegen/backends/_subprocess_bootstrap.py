"""Bootstrap that runs an untrusted codegen script with reduced builtins.

Invoked as ``python -I <bootstrap> <user_script> <source_path>``. It executes
the user script under a builtins namespace stripped of dynamic-execution
primitives (``eval``/``exec``/``compile``/``breakpoint``/``input``) that a
legitimate spreadsheet parser never needs.

This is defense-in-depth only, NOT a security boundary: ``__import__`` must stay
available for the script's ``import`` statements, so a determined escape is still
possible. Real isolation requires DockerBackend.
"""

import builtins
import sys

# ^ Primitives a parser never needs; removing them raises the bar for casual escapes.
_STRIPPED_BUILTINS = ("eval", "exec", "compile", "breakpoint", "input")


def _main() -> int:
    # ^ argv: [bootstrap, user_script_path, source_path]
    if len(sys.argv) < 3:
        print("bootstrap: expected <user_script> <source_path>", file=sys.stderr)
        return 2

    user_script = sys.argv[1]
    source_path = sys.argv[2]

    with open(user_script, encoding="utf-8") as f:
        code = f.read()

    safe_builtins: dict[str, object] = {name: getattr(builtins, name) for name in dir(builtins)}
    for name in _STRIPPED_BUILTINS:
        safe_builtins.pop(name, None)

    script_globals: dict[str, object] = {
        "__name__": "__main__",
        "__file__": user_script,
        "__builtins__": safe_builtins,
    }

    # ^ Present the user script with the argv it expects (source path as argv[1]),
    #   and compile with the real filename so tracebacks keep correct line numbers
    #   for the self-correction loop.
    sys.argv = [user_script, source_path]
    compiled = builtins.compile(code, user_script, "exec")
    exec(compiled, script_globals)  # noqa: S102
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
