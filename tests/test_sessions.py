from collections.abc import Sequence

import hashlib
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from PIL import Image

from openai.types.responses import ResponseOutputMessage, ResponseOutputText

from faltoobot import sessions
from faltoobot.gpt_utils import MessageHistory, get_tools_definition


def _listed_name(session: sessions.Session, name: str) -> str:
    return sessions._session_label(name, session.messages_path)


def _without_created_at(messages: MessageHistory) -> MessageHistory:
    return [
        {key: value for key, value in item.items() if key != "created_at"}
        for item in messages
    ]


def _fake_output_item(
    payload: dict[str, Any],
) -> ResponseOutputMessage | dict[str, Any]:
    if payload.get("type") != "message" or payload.get("role") != "assistant":
        return payload
    content = payload.get("content")
    if not isinstance(content, list):
        return payload
    return ResponseOutputMessage(
        id=str(payload.get("id") or "msg_fake"),
        type="message",
        role="assistant",
        status="completed",
        content=[
            ResponseOutputText(
                type="output_text",
                text=str(part.get("text") or ""),
                annotations=[],
            )
            for part in content
            if isinstance(part, dict) and part.get("type") == "output_text"
        ],
    )


class FakeResponse:
    def __init__(
        self,
        output: list[dict[str, Any]],
        usage: dict[str, Any] | None = None,
    ) -> None:
        self.output = [_fake_output_item(item) for item in output]
        self.usage = usage
        self.output_text = ""
        for item in self.output:
            if not isinstance(item, ResponseOutputMessage):
                continue
            self.output_text = "".join(
                part.text
                for part in item.content
                if isinstance(part, ResponseOutputText)
            ).strip()
            if self.output_text:
                break


class FakeUpload:
    def __init__(self, file_id: str) -> None:
        self.id = file_id


class FakeCompletedEvent:
    def __init__(self, output: list[dict[str, Any]], usage: dict[str, Any]) -> None:
        self.type = "response.completed"
        self.response = FakeResponse(output, usage)


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
    chat_key = "code@test"

    session = sessions.get_session(chat_key=chat_key)
    payload = sessions.get_messages(session)

    assert isinstance(session, sessions.Session)
    assert payload["id"] == session.session_id
    assert payload["chat_key"] == chat_key
    assert payload["system_prompt"] == ""
    assert _without_created_at(payload["messages"]) == []
    assert payload["message_ids"] == []
    assert Path(payload["workspace"]).is_dir()
    assert (Path(payload["workspace"]) / "AGENTS.md").exists()
    assert (
        tmp_path
        / ".faltoobot"
        / "sessions"
        / chat_key
        / session.session_id
        / "messages.json"
    ).exists()
    assert (tmp_path / ".faltoobot" / "sessions" / chat_key / "last_used").read_text(
        encoding="utf-8"
    ) == f"{session.session_id}\n"
    assert not (
        tmp_path / ".faltoobot" / "sessions" / chat_key / "sessions.json"
    ).exists()


def _config(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        root=tmp_path / ".faltoobot",
        openai_model="gpt-5-mini",
        openai_api_key="test",
        openai_oauth="",
        openai_thinking="low",
        openai_fast=False,
    )


async def run_answer(
    session: sessions.Session,
    question: str,
    attachments: Sequence[sessions.Attachment] | None = None,
    message_id: str | None = None,
) -> str:
    stored = await sessions.append_user_turn(
        session,
        question=question,
        attachments=attachments,
        message_ids=[message_id] if message_id else [],
    )
    if message_id and not stored:
        return ""
    return await sessions.get_answer(session)


def test_get_dir_chat_key_supports_subagent_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    workspace = tmp_path / "workspace"

    assert sessions.get_dir_chat_key(
        workspace, is_sub_agent=True
    ) == sessions.get_dir_chat_key(workspace).replace(
        "code@",
        "sub-agent@",
        1,
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
    assert payload["chat_key"] == chat_key
    assert chat_key == (
        f"code@{workspace.resolve().name}-"
        f"{hashlib.md5(str(workspace.resolve()).encode('utf-8')).hexdigest()[-6:]}"
    )
    assert (session.chat_root / "last_used").read_text(
        encoding="utf-8"
    ) == f"{session.session_id}\n"
    assert sessions.get_session(chat_key=chat_key) == session


def test_get_session_reads_last_used_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    chat_key = "123@lid"

    first = sessions.get_session(chat_key=chat_key, session_id="first")
    sessions.get_session(chat_key=chat_key, session_id="second")

    assert sessions.get_session(chat_key=chat_key).session_id == "second"

    sessions.set_last_used(first)
    payload = sessions.get_messages(sessions.get_session(chat_key=chat_key))

    assert payload["id"] == first.session_id


def test_get_session_warns_and_picks_any_session_without_last_used(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    chat_key = "123@lid"
    session = sessions.get_session(chat_key=chat_key, session_id="first")
    (session.chat_root / "last_used").unlink()

    picked = sessions.get_session(chat_key=chat_key)

    assert picked.session_id == "first"
    assert "Missing last_used for 123@lid" in caplog.text


def test_set_session_name_persists_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    session = sessions.get_session(chat_key="code@test")
    old_session_dir = session.session_dir

    sessions.set_session_name(session, "Fix flaky tests")
    payload = sessions.get_messages(session)

    assert session.session_id == "Fix flaky tests"
    assert payload["id"] == "Fix flaky tests"
    assert (session.chat_root / "last_used").read_text(
        encoding="utf-8"
    ) == "Fix flaky tests\n"
    assert not old_session_dir.exists()
    assert session.session_dir.exists()
    assert sessions.list_sessions(session.chat_key) == [
        {
            "id": "Fix flaky tests",
            "name": _listed_name(session, "Fix flaky tests"),
        }
    ]


def test_set_session_name_keeps_last_used_when_renaming_inactive_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    first = sessions.get_session(chat_key="code@test", session_id="first")
    second = sessions.get_session(chat_key="code@test", session_id="second")

    sessions.set_session_name(first, "Renamed first")

    assert (second.chat_root / "last_used").read_text(encoding="utf-8") == "second\n"


def test_set_session_name_does_not_create_missing_last_used_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    session = sessions.get_session(chat_key="code@test", session_id="first")
    marker = session.chat_root / "last_used"
    marker.unlink()

    sessions.set_session_name(session, "Renamed first")

    assert not marker.exists()


def test_list_sessions_includes_unnamed_sessions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    first = sessions.get_session(chat_key="code@test", session_id="first")
    second = sessions.get_session(chat_key="code@test", session_id="second")
    sessions.set_session_name(first, "Fix flaky tests")

    assert sessions.list_sessions("code@test") == [
        {"id": "second", "name": _listed_name(second, "second")},
        {
            "id": "Fix flaky tests",
            "name": _listed_name(first, "Fix flaky tests"),
        },
    ]


def test_list_sessions_ignores_folders_without_messages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    session = sessions.get_session(chat_key="code@test", session_id="live")
    session.chat_root.joinpath("missing").mkdir()

    assert sessions.list_sessions("code@test") == [
        {"id": "live", "name": _listed_name(session, "live")}
    ]


@pytest.mark.anyio
async def test_get_answer_updates_messages_and_ignores_duplicate_message_id(  # noqa: PLR0915
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    monkeypatch.setattr(
        sessions,
        "build_config",
        lambda: _config(tmp_path),
    )
    monkeypatch.setattr(
        sessions,
        "get_system_instructions",
        lambda config, chat_key, workspace: "system prompt",
    )
    calls: list[MessageHistory] = []
    tool_defs: list[Any] = []

    async def fake_get_streaming_reply(
        config: Any,
        instructions: str,
        input: MessageHistory,
        tools: list[Any],
        prompt_cache_key: str | None = None,
    ):
        assert instructions.startswith("system prompt")
        calls.append(list(input))
        assert prompt_cache_key == session.session_id
        tool_defs.extend([get_tools_definition(tool) for tool in tools])
        input.append(
            cast(
                Any,
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "hello"}],
                },
            )
        )
        input[-1]["usage"] = {
            "input_tokens": 1,
            "output_tokens": 2,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 3,
        }
        yield FakeCompletedEvent(
            [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "hello"}],
                }
            ],
            {
                "input_tokens": 1,
                "output_tokens": 2,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 3,
            },
        )

    monkeypatch.setattr(sessions, "_get_streaming_reply", fake_get_streaming_reply)
    chat_key = "code@test"

    session = sessions.get_session(chat_key=chat_key)
    workspace = Path(sessions.get_messages(session)["workspace"])
    (workspace / ".skills").mkdir(parents=True, exist_ok=True)
    (workspace / ".skills" / "pytest-helper.md").write_text(
        "---\ndescription: Write small pytest e2e checks\n---\nAlways keep tests small.\n",
        encoding="utf-8",
    )
    answer = await run_answer(
        session=session,
        question="Hi",
        message_id="msg-1",
    )
    duplicate = await run_answer(
        session=session,
        question="Hi again",
        message_id="msg-1",
    )
    payload = sessions.get_messages(session)

    assert answer == "hello"
    assert payload["system_prompt"] == "system prompt"
    assert duplicate == ""
    assert len(calls) == 1
    assert _without_created_at(calls[0]) == [
        {
            "type": "message",
            "role": "user",
            "content": "Hi",
        }
    ]
    tool_defs_by_name = {tool_def["name"]: tool_def for tool_def in tool_defs}
    assert set(tool_defs_by_name) == {
        "run_shell_call",
        "load_image",
        "load_skill",
    }

    shell_tool = tool_defs_by_name["run_shell_call"]
    assert shell_tool["type"] == "function"
    assert shell_tool["strict"] is True
    assert shell_tool["description"].startswith(
        "Returns the output of a shell command. Use it to inspect files and run CLI tasks."
    )
    load_image_tool = tool_defs_by_name["load_image"]
    assert load_image_tool["type"] == "function"
    assert load_image_tool["strict"] is True
    assert load_image_tool["description"].startswith(
        "Load image files in supported image formats: jpeg, png, gif, or webp."
    )

    assert "Commands are run from" in shell_tool["description"]
    assert shell_tool["parameters"] == {
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

    skills_tool = tool_defs_by_name["load_skill"]
    assert skills_tool["type"] == "function"
    assert skills_tool["strict"] is True
    assert skills_tool["description"].startswith(
        "The following skills provide specialized instructions for specific tasks."
    )
    assert skills_tool["parameters"] == {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "Exact local skill name to load.",
            },
        },
        "required": ["skill_name"],
        "additionalProperties": False,
    }
    assert payload["message_ids"] == ["msg-1"]
    assert _without_created_at(payload["messages"]) == [
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
async def test_get_answer_exposes_shell_tool_for_whatsapp_chats(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    monkeypatch.setattr(sessions, "build_config", lambda: _config(tmp_path))
    monkeypatch.setattr(
        sessions,
        "get_system_instructions",
        lambda config, chat_key, workspace: "system prompt",
    )
    tool_defs: list[Any] = []

    async def fake_get_streaming_reply(
        config: Any,
        instructions: str,
        input: MessageHistory,
        tools: list[Any],
        prompt_cache_key: str | None = None,
    ):
        tool_defs.extend([get_tools_definition(tool) for tool in tools])
        yield FakeCompletedEvent(
            [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "ok"}],
                }
            ],
            {
                "input_tokens": 1,
                "output_tokens": 1,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 2,
            },
        )

    monkeypatch.setattr(sessions, "_get_streaming_reply", fake_get_streaming_reply)

    session = sessions.get_session(chat_key="15551234567@s.whatsapp.net")
    answer = await run_answer(session=session, question="Hi")

    assert answer == "ok"
    tool_defs_by_name = {tool_def["name"]: tool_def for tool_def in tool_defs}
    shell_tool = tool_defs_by_name["run_shell_call"]
    assert shell_tool["description"].startswith(
        "Returns the output of a shell command. Use it to inspect files and run CLI tasks."
    )


@pytest.mark.anyio
async def test_get_answer_refreshes_whatsapp_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    monkeypatch.setattr(sessions, "build_config", lambda: _config(tmp_path))

    prompts = iter(["old prompt", "new prompt"])

    def fake_get_system_instructions(
        config: Any, chat_key: str, workspace: Path
    ) -> str:
        return next(prompts)

    monkeypatch.setattr(
        sessions, "get_system_instructions", fake_get_system_instructions
    )
    seen: list[str] = []

    async def fake_get_streaming_reply(
        config: Any,
        instructions: str,
        input: MessageHistory,
        tools: list[Any],
        prompt_cache_key: str | None = None,
    ):
        seen.append(instructions)
        reply = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "ok"}],
        }
        input.append(cast(Any, reply))
        yield FakeCompletedEvent(
            [reply],
            {
                "input_tokens": 1,
                "output_tokens": 1,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 2,
            },
        )

    monkeypatch.setattr(sessions, "_get_streaming_reply", fake_get_streaming_reply)

    session = sessions.get_session(chat_key="15551234567@s.whatsapp.net")
    assert await run_answer(session=session, question="one") == "ok"
    assert await run_answer(session=session, question="two") == "ok"

    payload = sessions.get_messages(session)
    assert seen == ["old prompt", "new prompt"]
    assert payload["system_prompt"] == "new prompt"


@pytest.mark.anyio
async def test_get_answer_refreshes_system_prompt_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    monkeypatch.setattr(sessions, "build_config", lambda: _config(tmp_path))

    calls = {"count": 0}

    def fake_get_system_instructions(
        config: Any, chat_key: str, workspace: Path
    ) -> str:
        calls["count"] += 1
        return f"system prompt {calls['count']}"

    monkeypatch.setattr(
        sessions, "get_system_instructions", fake_get_system_instructions
    )

    seen: list[str] = []

    async def fake_get_streaming_reply(
        config: Any,
        instructions: str,
        input: MessageHistory,
        tools: list[Any],
        prompt_cache_key: str | None = None,
    ):
        seen.append(instructions)
        reply = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "ok"}],
        }
        input.append(cast(Any, reply))
        yield FakeCompletedEvent(
            [reply],
            {
                "input_tokens": 1,
                "output_tokens": 1,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 2,
            },
        )

    monkeypatch.setattr(sessions, "_get_streaming_reply", fake_get_streaming_reply)

    session = sessions.get_session(chat_key="code@test")
    assert await run_answer(session=session, question="one") == "ok"
    assert await run_answer(session=session, question="two") == "ok"

    payload = sessions.get_messages(session)
    assert calls["count"] == len(seen)
    assert seen == ["system prompt 1", "system prompt 2"]
    assert payload["system_prompt"] == "system prompt 2"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case",
    [
        {
            "files": [("large.png", (2000, 1200), "red")],
            "question": "Look",
            "expected_uploads": 1,
            "expected_name_suffix": "1600x960.png",
            "expected_content": [
                {"type": "input_text", "text": "Look"},
                {"type": "input_image", "file_id": "file_123", "detail": "auto"},
            ],
        },
        {
            "files": [
                ("one.png", (8, 8), "red"),
                ("two.png", (8, 8), "blue"),
            ],
            "question": "compare",
            "expected_uploads": 2,
            "expected_name_suffix": None,
            "expected_content": [
                {"type": "input_text", "text": "compare"},
                {"type": "input_image", "file_id": "file_123", "detail": "auto"},
                {"type": "input_image", "file_id": "file_123", "detail": "auto"},
            ],
        },
    ],
)
async def test_get_answer_uploads_image_attachments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: dict[str, Any],
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    monkeypatch.setattr(sessions, "build_config", lambda: _config(tmp_path))
    monkeypatch.setattr(
        sessions,
        "get_system_instructions",
        lambda config, chat_key, workspace: "system prompt",
    )
    client = FakeClient()
    monkeypatch.setattr(sessions, "AsyncOpenAI", lambda api_key=None: client)

    async def fake_get_streaming_reply(
        config: Any,
        instructions: str,
        input: MessageHistory,
        tools: list[Any],
        prompt_cache_key: str | None = None,
    ):
        assert instructions.startswith("system prompt")
        if False:
            yield FakeCompletedEvent([], {})

    monkeypatch.setattr(sessions, "_get_streaming_reply", fake_get_streaming_reply)

    attachments: list[Path] = []
    for filename, size, color in cast(
        list[tuple[str, tuple[int, int], str]], case["files"]
    ):
        image = tmp_path / filename
        Image.new("RGB", size, color=color).save(image)
        attachments.append(image)

    session = sessions.get_session(
        chat_key="code@test",
        workspace=tmp_path / "workspace",
    )
    answer = await run_answer(
        session=session,
        question=str(case["question"]),
        attachments=attachments,
    )
    payload = sessions.get_messages(session)

    assert answer == ""
    assert len(client.files.calls) == cast(int, case["expected_uploads"])
    if case["expected_name_suffix"]:
        uploaded = client.files.calls[0]["file"]
        assert uploaded.name.endswith(str(case["expected_name_suffix"]))
    assert _without_created_at(payload["messages"]) == [
        {
            "type": "message",
            "role": "user",
            "content": cast(list[dict[str, Any]], case["expected_content"]),
        }
    ]
    assert client.closed is True


@pytest.mark.anyio
async def test_get_answer_uses_codex_output_when_completed_response_output_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_answer_streaming(session: sessions.Session, **_: Any):
        yield SimpleNamespace(
            type="response.completed",
            response=type(
                "Response",
                (),
                {
                    "output": [],
                    "output_text": "",
                    "codex_output": [
                        ResponseOutputMessage(
                            id="msg_codex",
                            type="message",
                            role="assistant",
                            status="completed",
                            content=[
                                ResponseOutputText(
                                    type="output_text",
                                    text="hello from codex",
                                    annotations=[],
                                )
                            ],
                        )
                    ],
                },
            )(),
        )

    monkeypatch.setattr(
        sessions,
        "get_answer_streaming",
        fake_get_answer_streaming,
    )

    answer = await sessions.get_answer(
        sessions.Session(chat_key="code@test", session_id="session-1")
    )

    assert answer == "hello from codex"


@pytest.mark.anyio
async def test_get_answer_uses_codex_output_when_output_text_property_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        output = None
        codex_output = [
            ResponseOutputMessage(
                id="msg_codex",
                type="message",
                role="assistant",
                status="completed",
                content=[
                    ResponseOutputText(
                        type="output_text",
                        text="hello from codex",
                        annotations=[],
                    )
                ],
            )
        ]

        @property
        def output_text(self) -> str:
            raise TypeError("'NoneType' object is not iterable")

    async def fake_get_answer_streaming(session: sessions.Session, **_: Any):
        yield SimpleNamespace(type="response.completed", response=Response())

    monkeypatch.setattr(sessions, "get_answer_streaming", fake_get_answer_streaming)

    answer = await sessions.get_answer(
        sessions.Session(chat_key="code@test", session_id="session-1")
    )

    assert answer == "hello from codex"


@pytest.mark.anyio
async def test_get_answer_uses_inline_images_for_chatgpt_oauth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    monkeypatch.setattr(
        sessions,
        "build_config",
        lambda: SimpleNamespace(
            root=tmp_path / ".faltoobot",
            openai_model="gpt-5-mini",
            openai_api_key="",
            openai_oauth="",
            openai_thinking="low",
            openai_fast=False,
        ),
    )
    monkeypatch.setattr(
        sessions,
        "get_system_instructions",
        lambda config, chat_key, workspace: "system prompt",
    )
    monkeypatch.setattr(sessions, "uses_chatgpt_oauth", lambda config: True)
    client = FakeClient()
    monkeypatch.setattr(sessions, "AsyncOpenAI", lambda api_key=None: client)

    async def fake_get_streaming_reply(
        config: Any,
        instructions: str,
        input: MessageHistory,
        tools: list[Any],
        prompt_cache_key: str | None = None,
    ):
        assert instructions.startswith("system prompt")
        if False:
            yield FakeCompletedEvent([], {})

    monkeypatch.setattr(sessions, "_get_streaming_reply", fake_get_streaming_reply)

    image = tmp_path / "small.png"
    Image.new("RGB", (8, 8), color="red").save(image)

    session = sessions.get_session(
        chat_key="code@test",
        workspace=tmp_path / "workspace",
    )
    answer = await run_answer(
        session=session,
        question="Look",
        attachments=[image],
    )
    payload = sessions.get_messages(session)

    assert answer == ""
    assert client.files.calls == []
    assert payload["messages"][0]["content"][1]["type"] == "input_image"
    assert payload["messages"][0]["content"][1]["image_url"].startswith(
        "data:image/png;base64,"
    )


@pytest.mark.anyio
async def test_append_user_turn_appends_user_content_and_message_ids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    monkeypatch.setattr(sessions, "build_config", lambda: _config(tmp_path))

    async def fake_upload_attachments(
        attachments: list[Path],
        workspace: Path,
        config: object,
    ) -> list[dict[str, Any]]:
        assert attachments
        return [{"type": "input_image", "file_id": "file_123", "detail": "auto"}]

    monkeypatch.setattr(sessions, "_upload_attachments", fake_upload_attachments)

    attachment = tmp_path / "one.png"
    attachment.write_bytes(b"png")
    session = sessions.get_session(chat_key="code@test")

    await sessions.append_user_turn(
        session,
        question="compare",
        attachments=[attachment],
        message_ids=["msg-1", "msg-2"],
    )

    assert sessions.get_messages(session)["message_ids"] == ["msg-1", "msg-2"]
    assert _without_created_at(sessions.get_messages(session)["messages"]) == [
        {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": "compare"},
                {"type": "input_image", "file_id": "file_123", "detail": "auto"},
            ],
        }
    ]


@pytest.mark.anyio
async def test_append_user_turn_logs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    monkeypatch.setattr(sessions, "build_config", lambda: _config(tmp_path))
    caplog.set_level(logging.INFO, logger="faltoobot")
    session = sessions.get_session(chat_key="code@test", session_id="session-1")

    await sessions.append_user_turn(session, question="Hi")

    assert any(
        record.message == "Appended user turn; attachments=0 message_ids=0"
        for record in caplog.records
    )


@pytest.mark.anyio
async def test_get_answer_reuses_existing_user_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    monkeypatch.setattr(sessions, "build_config", lambda: _config(tmp_path))
    monkeypatch.setattr(
        sessions,
        "get_system_instructions",
        lambda config, chat_key, workspace: "system prompt",
    )
    calls: list[MessageHistory] = []

    async def fake_get_streaming_reply(
        config: Any,
        instructions: str,
        input: MessageHistory,
        tools: list[Any],
        prompt_cache_key: str | None = None,
    ):
        calls.append(list(input))
        input.append(
            cast(
                Any,
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "hello"}],
                },
            )
        )
        yield FakeCompletedEvent(
            [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "hello"}],
                }
            ],
            {
                "input_tokens": 1,
                "output_tokens": 1,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 2,
            },
        )

    monkeypatch.setattr(sessions, "_get_streaming_reply", fake_get_streaming_reply)
    session = sessions.get_session(chat_key="code@test")
    await sessions.append_user_turn(session, question="Hi", message_ids=["msg-1"])

    answer = await sessions.get_answer(session)

    assert answer == "hello"
    user_item = calls[0][0]
    assert isinstance(user_item.pop("created_at"), str)
    assert calls == [
        [
            {
                "type": "message",
                "role": "user",
                "content": "Hi",
            }
        ]
    ]
    messages = sessions.get_messages(session)["messages"]
    assert isinstance(messages[0].pop("created_at"), str)
    assert isinstance(messages[1].pop("created_at"), str)
    assert messages == [
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
