from pathlib import Path
from types import SimpleNamespace
from typing import Any
import hashlib

import pytest
from openai.types.responses import ResponseInputParam
from PIL import Image

from faltoobot import sessions
from faltoobot.gpt_utils import get_tools_definition


class FakeItem:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def to_dict(self) -> dict[str, Any]:
        return self.payload


class FakeResponse:
    def __init__(self, output: list[dict[str, Any]]) -> None:
        self.output = [FakeItem(item) for item in output]


class FakeUpload:
    def __init__(self, file_id: str) -> None:
        self.id = file_id


class FakeCompletedEvent:
    def __init__(self, usage: dict[str, Any]) -> None:
        self.response = SimpleNamespace(usage=usage)


class FakeFiles:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> FakeUpload:
        self.calls.append(kwargs)
        return FakeUpload("file_123")


class FakeClient:
    def __init__(self) -> None:
        self.files = FakeFiles()
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def test_get_session_creates_messages_json_and_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    chat_key = "123@lid"

    session = sessions.get_session(chat_key=chat_key)
    payload = sessions.get_messages(session)

    assert payload["id"] == session[1]
    assert payload["chat_key"] == chat_key
    assert payload["messages"] == []
    assert payload["message_ids"] == []
    assert Path(payload["workspace"]).is_dir()
    assert (
        tmp_path / ".faltoobot" / "sessions" / chat_key / session[1] / "messages.json"
    ).exists()


def _config(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        root=tmp_path / ".faltoobot",
        system_prompt="system prompt",
        openai_model="gpt-5-mini",
        openai_api_key="test",
        openai_thinking="low",
        openai_fast=False,
    )


def test_get_session_sets_dir_chat_key_and_last_used(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    workspace = tmp_path / "workspace"
    chat_key = sessions.get_dir_chat_key(workspace)

    session = sessions.get_session(chat_key=chat_key, workspace=workspace)
    payload = sessions.get_messages(session)
    last_used = (
        (tmp_path / ".faltoobot" / "sessions" / chat_key / sessions.LAST_USED_FILE)
        .read_text(encoding="utf-8")
        .strip()
    )

    assert payload["chat_key"] == chat_key
    assert chat_key == (
        f"code@{workspace.resolve().name}:"
        f"{hashlib.md5(str(workspace.resolve()).encode('utf-8')).hexdigest()[-6:]}"
    )
    assert last_used == session[1]


def test_get_session_reads_last_used_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    chat_key = "123@lid"

    first = sessions.get_session(chat_key=chat_key)
    second = sessions.get_session(chat_key=chat_key)
    payload = sessions.get_messages(second)

    assert second == first
    assert payload["id"] == first[1]


@pytest.mark.anyio
async def test_get_answer_updates_messages_and_ignores_duplicate_message_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    monkeypatch.setattr(
        sessions,
        "build_config",
        lambda: _config(tmp_path),
    )
    calls: list[ResponseInputParam] = []
    tool_defs: list[Any] = []

    async def fake_get_streaming_reply(
        instructions: str,
        input: ResponseInputParam,
        tools: list[Any],
        api_key: str,
    ):
        assert api_key == "test"
        assert instructions.startswith("system prompt")
        calls.append(input)
        tool_defs.extend([get_tools_definition(tool) for tool in tools])
        yield FakeResponse(
            [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "hello"}],
                }
            ]
        )

    monkeypatch.setattr(sessions, "get_streaming_reply", fake_get_streaming_reply)
    chat_key = "123@lid"

    session = sessions.get_session(chat_key=chat_key)
    payload = await sessions.get_answer(
        session=session,
        question="Hi",
        message_id="msg-1",
    )
    duplicate = await sessions.get_answer(
        session=session,
        question="Hi again",
        message_id="msg-1",
    )

    assert len(calls) == 1
    assert calls[0] == [
        {
            "type": "message",
            "role": "user",
            "content": "Hi",
        }
    ]
    assert len(tool_defs) == 1
    tool_def = tool_defs[0]
    assert tool_def["type"] == "function"
    assert tool_def["name"] == "run_shell_call"
    assert tool_def["strict"] is True
    assert tool_def["description"].startswith(
        "Returns the output of a shell command. Use it to inspect files and run CLI tasks."
    )
    assert "Commands are run from" in tool_def["description"]
    assert tool_def["parameters"] == {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Bash command to run.",
            },
            "timeout_ms": {
                "type": "integer",
                "description": "Kill the command after this timeout in milliseconds.",
            },
        },
        "required": ["command", "timeout_ms"],
        "additionalProperties": False,
    }
    assert payload["message_ids"] == ["msg-1"]
    assert payload["messages"] == [
        {
            "type": "message",
            "role": "user",
            "content": "Hi",
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "hello"}],
        },
    ]
    assert duplicate == payload


@pytest.mark.anyio
async def test_get_answer_saves_usage_from_completed_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    monkeypatch.setattr(
        sessions,
        "build_config",
        lambda: _config(tmp_path),
    )

    async def fake_get_streaming_reply(
        instructions: str,
        input: ResponseInputParam,
        tools: list[Any],
        api_key: str,
    ):
        assert instructions.startswith("system prompt")
        yield FakeCompletedEvent(
            {
                "input_tokens": 1,
                "output_tokens": 2,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 3,
            }
        )
        yield FakeResponse(
            [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "hello"}],
                }
            ]
        )

    monkeypatch.setattr(sessions, "get_streaming_reply", fake_get_streaming_reply)

    session = sessions.get_session(chat_key="123@lid")
    payload = await sessions.get_answer(session=session, question="Hi")

    assert payload["messages"] == [
        {
            "type": "message",
            "role": "user",
            "content": "Hi",
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "hello"}],
            "usage": {
                "input_tokens": 1,
                "output_tokens": 2,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 3,
            },
        },
    ]


@pytest.mark.anyio
async def test_get_answer_uploads_and_resizes_image_attachments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    monkeypatch.setattr(
        sessions,
        "build_config",
        lambda: _config(tmp_path),
    )
    client = FakeClient()
    monkeypatch.setattr(sessions, "AsyncOpenAI", lambda api_key=None: client)

    async def fake_get_streaming_reply(
        instructions: str,
        input: ResponseInputParam,
        tools: list[Any],
        api_key: str,
    ):
        assert api_key == "test"
        assert instructions.startswith("system prompt")
        yield FakeResponse([])

    monkeypatch.setattr(sessions, "get_streaming_reply", fake_get_streaming_reply)

    image = tmp_path / "large.png"
    Image.new("RGB", (2000, 1200), color="red").save(image)

    chat_key = "123@lid"
    session = sessions.get_session(
        chat_key=chat_key,
        workspace=tmp_path / "workspace",
    )
    payload = await sessions.get_answer(
        session=session,
        question="Look",
        attachments=[image],
    )

    assert client.files.calls[0]["purpose"] == "vision"
    uploaded = client.files.calls[0]["file"]
    assert uploaded.name.endswith("1600x960.png")
    assert payload["messages"] == [
        {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Look"},
                {"type": "input_image", "file_id": "file_123", "detail": "auto"},
            ],
        }
    ]
    assert client.closed is True
