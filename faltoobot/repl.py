import os
import traceback
from collections.abc import Mapping
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from threading import Lock
from typing import TypedDict


class ReplResult(TypedDict):
    stdout: str
    stderr: str
    raised: bool


_PYTHON_REPL_SESSIONS: dict[str, dict[str, object]] = {}
_PYTHON_REPL_EXECUTION_LOCK = Lock()


def _python_repl_session(session_key: str, continue_session: bool) -> dict[str, object]:
    if not continue_session or session_key not in _PYTHON_REPL_SESSIONS:
        _PYTHON_REPL_SESSIONS[session_key] = {
            "__name__": "__main__",
            "__builtins__": __builtins__,
        }
    return _PYTHON_REPL_SESSIONS[session_key]


def run_python_script_in_session(
    session_key: str,
    workspace: str,
    script: str,
    continue_session: bool,
    env_overrides: Mapping[str, str] | None = None,
) -> ReplResult:
    workspace = str(Path(workspace).expanduser().resolve())
    stdout = StringIO()
    stderr = StringIO()
    try:
        # comment: os.chdir is process-global, so all REPL executions must stay serialized.
        with _PYTHON_REPL_EXECUTION_LOCK:
            session = _python_repl_session(session_key, continue_session)
            cwd = os.getcwd()
            old_env = {key: os.environ.get(key) for key in env_overrides or {}}
            try:
                if env_overrides:
                    # comment: Python tools should see configured API keys without clearing unrelated env.
                    os.environ.update(env_overrides)
                os.chdir(workspace)
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exec(compile(script, "<python-shell>", "exec"), session)
            finally:
                os.chdir(cwd)
                for key, value in old_env.items():
                    # comment: restore only the keys we overrode for this tool call.
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
    except (Exception, SystemExit, KeyboardInterrupt):
        stderr.write(traceback.format_exc())
        return {
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "raised": True,
        }
    return {
        "stdout": stdout.getvalue(),
        "stderr": stderr.getvalue(),
        "raised": False,
    }
