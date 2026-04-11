import json
import os
import subprocess
from collections.abc import Callable
from collections.abc import Awaitable
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

MAX_SHELL_OUTPUT = 12_000
ToolOutput = str | list[ResponseInputText | ResponseInputImage | ResponseInputFile]


def _clipped_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    return (value or "")[:MAX_SHELL_OUTPUT]


def _tool_env() -> dict[str, str]:
    env = dict(os.environ)
    config = build_config()
    if config.gemini_api_key:
        # comment: image-generation shell examples use google-genai, which expects the Gemini key
        # in the process environment rather than in code snippets.
        env["GEMINI_API_KEY"] = config.gemini_api_key
    return env


def run_shell_call_in_workspace(workspace: str, command: str, timeout_ms: int) -> str:
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
