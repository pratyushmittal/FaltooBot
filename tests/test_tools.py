import json
from pathlib import Path
from typing import Any, cast

from faltoobot.gpt_utils import get_tools_definition
from faltoobot.tools import get_run_shell_call_tool, run_shell_call_in_workspace


def test_get_run_shell_call_tool_builds_valid_tool_definition(tmp_path: Path) -> None:
    tool = get_run_shell_call_tool(tmp_path)
    definition = get_tools_definition(tool)

    description = cast(str, definition["description"])
    parameters = cast(dict[str, Any], definition["parameters"])

    assert definition["type"] == "function"
    assert definition["name"] == "run_shell_call"
    assert definition["strict"] is True
    assert description.startswith(
        "Returns the output of a shell command. Use it to inspect files and run CLI tasks."
    )
    assert "Commands are run from" in description
    assert parameters == {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Bash command to run.",
            },
            "command_summary": {
                "type": "string",
                "description": "A short one-line summary of what the command is doing. Keep it brief.",
            },
            "timeout_ms": {
                "type": "integer",
                "description": "Kill the command after this timeout in milliseconds.",
            },
        },
        "required": ["command", "command_summary", "timeout_ms"],
        "additionalProperties": False,
    }


def test_run_shell_call_in_workspace_runs_in_workspace(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("world\n", encoding="utf-8")

    result = json.loads(
        run_shell_call_in_workspace(
            str(tmp_path),
            "pwd; printf x; cat hello.txt",
            timeout_ms=5000,
        )
    )

    assert result["stderr"] == ""
    assert result["exit_code"] == 0
    assert result["timed_out"] is False
    assert str(tmp_path) in result["stdout"]
    assert "xworld" in result["stdout"]
