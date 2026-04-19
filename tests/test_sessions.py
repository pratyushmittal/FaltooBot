from collections.abc import Sequence

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from PIL import Image

from openai.types.responses import ResponseOutputMessage, ResponseOutputText

from faltoobot import sessions
from faltoobot.gpt_utils import MessageHistory, get_tools_definition


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

    assert payload["id"] == session[1]
    assert payload["chat_key"] == chat_key
    assert payload["system_prompt"] == ""
    assert payload["messages"] == []
    assert payload["message_ids"] == []
    assert Path(payload["workspace"]).is_dir()
    assert (Path(payload["workspace"]) / "AGENTS.md").exists()
    assert (
        tmp_path / ".faltoobot" / "sessions" / chat_key / session[1] / "messages.json"
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
    last_used = (
        (tmp_path / ".faltoobot" / "sessions" / chat_key / sessions.LAST_USED_FILE)
        .read_text(encoding="utf-8")
        .strip()
    )

    assert payload["chat_key"] == chat_key
    assert chat_key == (
        f"code@{workspace.resolve().name}-"
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
        instructions: str,
        input: MessageHistory,
        tools: list[Any],
        prompt_cache_key: str | None = None,
    ):
        assert instructions.startswith("system prompt")
        calls.append(list(input))
        assert prompt_cache_key == session[1]
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

    monkeypatch.setattr(sessions, "get_streaming_reply", fake_get_streaming_reply)
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
    assert calls[0] == [
        {
            "type": "message",
            "role": "user",
            "content": "Hi",
        }
    ]
    tool_defs_by_name = {tool_def["name"]: tool_def for tool_def in tool_defs}
    assert set(tool_defs_by_name) == {
        "run_shell_call",
        "run_in_python_shell",
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
        "Load image files such as jpg or png. Useful for seeing screenshots and creatives."
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

    python_tool = tool_defs_by_name["run_in_python_shell"]
    assert python_tool["type"] == "function"
    assert python_tool["strict"] is True
    assert python_tool["description"].startswith(
        "Run Python code in a persistent interpreter session."
    )
    assert "multi-turn" in python_tool["description"]
    assert "Returns the output of stdout and stderr." in python_tool["description"]
    assert python_tool["parameters"] == {
        "type": "object",
        "properties": {
            "script": {
                "type": "string",
                "description": "Python code to execute. Use `print(...)` to inspect values.",
            },
            "continue_session": {
                "type": "boolean",
                "description": "Whether to reuse the previous Python session for this workspace.",
            },
        },
        "required": ["script", "continue_session"],
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

    monkeypatch.setattr(sessions, "get_streaming_reply", fake_get_streaming_reply)

    session = sessions.get_session(chat_key="15551234567@s.whatsapp.net")
    answer = await run_answer(session=session, question="Hi")

    assert answer == "ok"
    tool_defs_by_name = {tool_def["name"]: tool_def for tool_def in tool_defs}
    shell_tool = tool_defs_by_name["run_shell_call"]
    assert shell_tool["description"].startswith(
        "Returns the output of a shell command. Use it to inspect files and run CLI tasks."
    )


@pytest.mark.anyio
async def test_get_answer_caches_system_prompt_per_session(
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

    async def fake_get_streaming_reply(
        instructions: str,
        input: MessageHistory,
        tools: list[Any],
        prompt_cache_key: str | None = None,
    ):
        assert instructions == "system prompt 1"
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

    monkeypatch.setattr(sessions, "get_streaming_reply", fake_get_streaming_reply)

    session = sessions.get_session(chat_key="code@test")
    assert await run_answer(session=session, question="one") == "ok"
    assert await run_answer(session=session, question="two") == "ok"

    payload = sessions.get_messages(session)
    assert calls["count"] == 1
    assert payload["system_prompt"] == "system prompt 1"


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
        instructions: str,
        input: MessageHistory,
        tools: list[Any],
        prompt_cache_key: str | None = None,
    ):
        assert instructions.startswith("system prompt")
        if False:
            yield FakeCompletedEvent([], {})

    monkeypatch.setattr(sessions, "get_streaming_reply", fake_get_streaming_reply)

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
    assert payload["messages"] == [
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

    answer = await sessions.get_answer(("code@test", "session-1"))

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
        instructions: str,
        input: MessageHistory,
        tools: list[Any],
        prompt_cache_key: str | None = None,
    ):
        assert instructions.startswith("system prompt")
        if False:
            yield FakeCompletedEvent([], {})

    monkeypatch.setattr(sessions, "get_streaming_reply", fake_get_streaming_reply)

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
    assert sessions.get_messages(session)["messages"] == [
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

    monkeypatch.setattr(sessions, "get_streaming_reply", fake_get_streaming_reply)
    session = sessions.get_session(chat_key="code@test")
    await sessions.append_user_turn(session, question="Hi", message_ids=["msg-1"])

    answer = await sessions.get_answer(session)

    assert answer == "hello"
    assert calls == [
        [
            {
                "type": "message",
                "role": "user",
                "content": "Hi",
            }
        ]
    ]
    assert sessions.get_messages(session)["messages"] == [
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
