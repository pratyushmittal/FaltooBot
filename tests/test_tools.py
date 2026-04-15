import json
from pathlib import Path
from typing import Any, cast

import pytest
from openai.types.responses import ResponseInputImage

from faltoobot import images, tools
from faltoobot.gpt_utils import get_tools_definition
from faltoobot.tools import (
    get_load_image_tool,
    get_run_shell_call_tool,
    load_image_in_workspace,
    run_shell_call_in_workspace,
)


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


def test_run_shell_call_in_workspace_sets_gemini_key_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        tools,
        "build_config",
        lambda: type("Config", (), {"gemini_api_key": "gem-key"})(),
        raising=False,
    )

    result = json.loads(
        run_shell_call_in_workspace(
            str(tmp_path),
            """python - <<'PY'
import os
print(os.environ.get("GEMINI_API_KEY", ""))
PY""",
            timeout_ms=5000,
        )
    )

    assert result["stderr"] == ""
    assert result["exit_code"] == 0
    assert result["timed_out"] is False
    assert "gem-key" in result["stdout"]


def test_run_shell_call_in_workspace_blocks_faltoobot_config_access(
    tmp_path: Path,
) -> None:
    result = json.loads(
        run_shell_call_in_workspace(
            str(tmp_path),
            "cat ~/.faltoobot/config.toml",
            timeout_ms=5000,
            allow_faltoobot_config_access=False,
        )
    )

    assert result["stdout"] == ""
    assert result["exit_code"] == 1
    assert result["timed_out"] is False
    assert "cannot read or modify Faltoobot config files" in result["stderr"]


def test_get_load_image_tool_builds_valid_tool_definition(tmp_path: Path) -> None:
    tool = get_load_image_tool(tmp_path)
    definition = get_tools_definition(tool)

    description = cast(str, definition["description"])
    parameters = cast(dict[str, Any], definition["parameters"])

    assert definition["type"] == "function"
    assert definition["name"] == "load_image"
    assert definition["strict"] is True
    assert description.startswith(
        "Load image files such as jpg or png. Useful for seeing screenshots and creatives."
    )
    assert parameters == {
        "type": "object",
        "properties": {
            "image_path": {
                "type": "string",
                "description": "relative or absolute path of the image",
            },
        },
        "required": ["image_path"],
        "additionalProperties": False,
    }


@pytest.mark.anyio
async def test_load_image_in_workspace_returns_inline_images_for_oauth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "browser-home.png"
    image.write_bytes(b"png")

    monkeypatch.setattr(tools, "build_config", lambda: object(), raising=False)
    monkeypatch.setattr(tools, "uses_chatgpt_oauth", lambda config: True, raising=False)
    monkeypatch.setattr(
        images,
        "inline_image_item",
        lambda workspace, source: ResponseInputImage(
            type="input_image",
            image_url=f"file://{source}",
            detail="auto",
        ),
    )

    result = await load_image_in_workspace(
        str(tmp_path),
        "browser-home.png",
    )

    assert len(result) == 1
    assert isinstance(result, list)
    item = result[0]
    assert isinstance(item, ResponseInputImage)
    assert item.type == "input_image"
    assert item.image_url == f"file://{image.resolve()}"
    assert item.detail == "auto"


@pytest.mark.anyio
async def test_load_image_in_workspace_returns_uploaded_images_for_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "browser-home.png"
    image.write_bytes(b"png")
    closed: list[str] = []

    class FakeClient:
        async def close(self) -> None:
            closed.append("closed")

    monkeypatch.setattr(tools, "build_config", lambda: object(), raising=False)
    monkeypatch.setattr(
        tools, "uses_chatgpt_oauth", lambda config: False, raising=False
    )
    monkeypatch.setattr(
        tools, "get_openai_client", lambda config: FakeClient(), raising=False
    )

    async def fake_upload_attachment(client, workspace, source):
        return ResponseInputImage(
            type="input_image",
            file_id=f"file:{source.name}",
            detail="auto",
        )

    monkeypatch.setattr(images, "upload_attachment", fake_upload_attachment)

    result = await load_image_in_workspace(
        str(tmp_path),
        "browser-home.png",
    )

    assert len(result) == 1
    assert isinstance(result, list)
    item = result[0]
    assert isinstance(item, ResponseInputImage)
    assert item.type == "input_image"
    assert item.file_id == "file:browser-home.png"
    assert item.detail == "auto"
    assert closed == ["closed"]
