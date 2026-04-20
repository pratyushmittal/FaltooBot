import json
import os
import subprocess
import sys
from collections.abc import Awaitable
from collections.abc import Callable
from pathlib import Path

from openai.types.responses import (
    ResponseInputFile,
    ResponseInputImage,
    ResponseInputText,
)

from faltoobot import images
from faltoobot.config import build_config
from faltoobot.gpt_utils import get_openai_client
from faltoobot.openai_auth import uses_chatgpt_oauth
from faltoobot.repl import run_python_script_in_session

MAX_SHELL_OUTPUT = 12_000
ToolOutput = str | list[ResponseInputText | ResponseInputImage | ResponseInputFile]


def _clipped_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    return (value or "")[:MAX_SHELL_OUTPUT]


def _tool_env() -> dict[str, str]:
    env = dict(os.environ)
    tool_python = Path(sys.executable).resolve()
    tool_bin = str(tool_python.parent)
    tool_env = str(tool_python.parent.parent)
    env["PATH"] = f"{tool_bin}:{env.get('PATH', '')}".rstrip(":")
    env["VIRTUAL_ENV"] = tool_env
    config = build_config()
    if config.gemini_api_key:
        # comment: image-generation shell examples use google-genai, which expects the Gemini key
        # in the process environment rather than in code snippets.
        env["GEMINI_API_KEY"] = config.gemini_api_key
    return env


def run_shell_call_in_workspace(
    workspace: str,
    command: str,
    timeout_ms: int,
) -> str:
    try:
        process = subprocess.run(
            ["/bin/bash", "-lc", command],
            capture_output=True,
            text=False,
            timeout=timeout_ms / 1000,
            cwd=workspace,
            env=_tool_env(),
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


def get_run_in_python_shell_tool(
    workspace: Path,
    session_key: str | None = None,
) -> Callable[[str, bool], str]:
    workspace = workspace.expanduser().resolve()
    workspace_str = str(workspace)
    repl_session_key = workspace_str if session_key is None else session_key

    def run_in_python_shell(script: str, continue_session: bool) -> str:
        result = run_python_script_in_session(
            repl_session_key,
            workspace_str,
            script,
            continue_session,
        )
        return json.dumps(
            {
                "stdout": _clipped_text(result["stdout"]),
                "stderr": _clipped_text(result["stderr"]),
                "raised": result["raised"],
            }
        )

    run_in_python_shell.__doc__ = f"""Run Python code in a persistent interpreter session. Use it for multi-turn execution in tool calls where you need to check one step's output before the next. Especially useful for Python-based skills.

    Returns the output of stdout and stderr.

    Code runs from `{workspace}` directory.

    Use `print(...)` to inspect values.

    Args:
        - script: Python code to execute. Use `print(...)` to inspect values.
        - continue_session: Whether to reuse the previous Python session for this workspace.
    """
    return run_in_python_shell


async def load_image_in_workspace(workspace: str, image_path: str) -> ToolOutput:
    path = Path(image_path)
    workspace_path = Path(workspace).resolve()
    if not path.is_absolute():
        path = workspace_path / path
    resolved = path.resolve()
    config = build_config()

    if uses_chatgpt_oauth(config):
        return [images.inline_image_item(workspace_path, resolved)]

    client = get_openai_client(config)
    try:
        return [await images.upload_attachment(client, workspace_path, resolved)]
    finally:
        await client.close()


def get_load_image_tool(workspace: Path) -> Callable[[str], Awaitable[ToolOutput]]:
    workspace = workspace.expanduser().resolve()

    async def load_image(image_path: str) -> ToolOutput:
        return await load_image_in_workspace(str(workspace), image_path)

    load_image.__doc__ = """Load image files such as jpg or png. Useful for seeing screenshots and creatives.

    Args:
        - image_path: relative or absolute path of the image
    """
    return load_image
