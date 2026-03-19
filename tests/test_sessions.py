from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from faltoobot import sessions


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



def test_get_session_id_creates_messages_json_and_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")

    session_id = sessions.get_session_id()
    payload = sessions.get_messages(session_id)

    assert payload["id"] == session_id
    assert payload["kind"] == "whatsapp"
    assert payload["messages"] == []
    assert payload["message_ids"] == []
    assert Path(payload["workspace"]).is_dir()
    assert (tmp_path / ".faltoobot" / "sessions" / session_id / "messages.json").exists()


@pytest.mark.anyio
async def test_get_answer_updates_messages_and_ignores_duplicate_message_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    monkeypatch.setattr(
        sessions,
        "build_config",
        lambda: SimpleNamespace(openai_model="gpt-5-mini", openai_api_key="test"),
    )
    calls: list[list[dict[str, Any]]] = []

    async def fake_get_streaming_reply(
        model: str,
        input: list[Any],
        tools: list[Any],
    ):
        calls.append(input)
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

    session_id = sessions.get_session_id()
    payload = await sessions.get_answer(
        session_id=session_id,
        question="Hi",
        message_id="msg-1",
    )
    duplicate = await sessions.get_answer(
        session_id=session_id,
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
async def test_get_answer_uploads_and_resizes_image_attachments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    monkeypatch.setattr(
        sessions,
        "build_config",
        lambda: SimpleNamespace(openai_model="gpt-5-mini", openai_api_key="test"),
    )
    client = FakeClient()
    monkeypatch.setattr(sessions, "AsyncOpenAI", lambda api_key=None: client)

    async def fake_get_streaming_reply(
        model: str,
        input: list[Any],
        tools: list[Any],
    ):
        yield FakeResponse([])

    monkeypatch.setattr(sessions, "get_streaming_reply", fake_get_streaming_reply)

    image = tmp_path / "large.png"
    Image.new("RGB", (2000, 1200), color="red").save(image)

    session_id = sessions.get_session_id(workspace=tmp_path / "workspace")
    payload = await sessions.get_answer(
        session_id=session_id,
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
