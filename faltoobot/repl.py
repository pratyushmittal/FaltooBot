import os
import traceback
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
) -> ReplResult:
    workspace = str(Path(workspace).expanduser().resolve())
    stdout = StringIO()
    stderr = StringIO()
    try:
        # comment: os.chdir is process-global, so all REPL executions must stay serialized.
        with _PYTHON_REPL_EXECUTION_LOCK:
            session = _python_repl_session(session_key, continue_session)
            cwd = os.getcwd()
            try:
                os.chdir(workspace)
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exec(compile(script, "<python-shell>", "exec"), session)
            finally:
                os.chdir(cwd)
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
