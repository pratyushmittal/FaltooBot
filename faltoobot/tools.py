import json
import subprocess
from collections.abc import Callable
from pathlib import Path

MAX_SHELL_OUTPUT = 12_000


def _clipped_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    return (value or "")[:MAX_SHELL_OUTPUT]


def run_shell_call_in_workspace(workspace: str, command: str, timeout_ms: int) -> str:
    try:
        process = subprocess.run(
            ["/bin/bash", "-lc", command],
            capture_output=True,
            text=False,
            timeout=timeout_ms / 1000,
            cwd=workspace,
        )
    except subprocess.TimeoutExpired as exc:
        result = {
            "stdout": _clipped_text(exc.stdout),
            "stderr": _clipped_text(exc.stderr),
            "exit_code": None,
            "timed_out": True,
        }
    except Exception as exc:  # comment: tool failures should be returned to the model, not crash the chat.
        result = {
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "exit_code": None,
            "timed_out": False,
        }
    else:
        result = {
            "stdout": _clipped_text(process.stdout),
            "stderr": _clipped_text(process.stderr),
            "exit_code": process.returncode,
            "timed_out": False,
        }
    return json.dumps(result)


def get_run_shell_call_tool(workspace: Path) -> Callable[[str, str, int], str]:
    workspace = workspace.expanduser().resolve()

    def run_shell_call(command: str, command_summary: str, timeout_ms: int) -> str:
        return run_shell_call_in_workspace(str(workspace), command, timeout_ms)

    run_shell_call.__doc__ = f"""Returns the output of a shell command. Use it to inspect files and run CLI tasks.

    Commands are run from `{workspace}` directory.

    Args:
        - command: Bash command to run.
        - command_summary: A short one-line summary of what the command is doing. Keep it brief.
        - timeout_ms: Kill the command after this timeout in milliseconds.
    """
    return run_shell_call
