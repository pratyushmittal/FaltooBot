import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pytest_bdd import given, scenarios, then, when

from faltoobot import gpt_utils, sessions
from faltoobot.config import Config
from faltoobot.gpt_utils import MessageHistory

scenarios("features/message_timestamps.feature")


@pytest.fixture
def timestamp_ctx(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    monkeypatch.setattr(
        sessions,
        "build_config",
        lambda: cast(
            Config,
            SimpleNamespace(
                root=tmp_path / ".faltoobot",
                openai_model="gpt-5-mini",
                openai_api_key="test-key",
                openai_oauth="",
                openai_thinking="low",
                openai_fast=False,
            ),
        ),
    )
    monkeypatch.setattr(
        sessions,
        "get_system_instructions",
        lambda config, chat_key, workspace: "system prompt",
    )
    return {}


@given("a Faltoochat session with a mocked text response")
def faltoochat_session_with_mocked_text_response(
    timestamp_ctx: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_get_streaming_reply(
        config: Any,
        instructions: str,
        input: MessageHistory,
        tools: list[Any],
        prompt_cache_key: str | None = None,
    ):
        input.append(
            gpt_utils._to_message_item(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "hello"}],
                }
            )
        )
        yield SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                output=[],
                output_text="hello",
                usage=SimpleNamespace(to_dict=lambda: {"total_tokens": 1}),
            ),
        )

    monkeypatch.setattr(sessions, "_get_streaming_reply", fake_get_streaming_reply)
    timestamp_ctx["session"] = sessions.get_session(chat_key="code@test")


@when("I ask a question")
def ask_question(timestamp_ctx: dict[str, Any]) -> None:
    async def run() -> None:
        session = cast(sessions.Session, timestamp_ctx["session"])
        await sessions.append_user_turn(session, question="Hi")
        await sessions.get_answer(session)

    asyncio.run(run())


@then(
    "the session's messages.json contains timestamps for my question and the response"
)
def messages_json_contains_timestamps(timestamp_ctx: dict[str, Any]) -> None:
    session = cast(sessions.Session, timestamp_ctx["session"])
    messages = json.loads(session.messages_path.read_text(encoding="utf-8"))["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "Hi"
    assert all(isinstance(message.get("timestamp"), str) for message in messages)
