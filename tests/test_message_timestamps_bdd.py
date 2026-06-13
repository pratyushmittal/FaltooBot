import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pytest_bdd import given, scenarios, then, when

from faltoobot import gpt_utils, sessions
from faltoobot.config import Config
from faltoobot.gpt_utils import MessageHistory

scenarios("features/message_timestamps.feature")


@pytest.fixture
def timestamp_ctx(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> dict[str, Any]:
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
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "hello"}],
            }
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


@when("I ask a timestamped question")
def ask_timestamped_question(timestamp_ctx: dict[str, Any]) -> None:
    async def run() -> None:
        session = cast(sessions.Session, timestamp_ctx["session"])
        await sessions.append_user_turn(session, question="Hi")
        await sessions.get_answer(session)

    asyncio.run(run())


@then("the local transcript stores timestamps for the new messages")
def transcript_stores_timestamps(timestamp_ctx: dict[str, Any]) -> None:
    session = cast(sessions.Session, timestamp_ctx["session"])
    messages = sessions.get_messages(session)["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert all(isinstance(message.get("created_at"), str) for message in messages)


@given("a saved text message with a timestamp")
def saved_text_message_with_timestamp(timestamp_ctx: dict[str, Any]) -> None:
    timestamp_ctx["history"] = [
        {
            "type": "message",
            "role": "user",
            "content": "Hi",
            "created_at": "2026-06-13T17:24:33+05:30",
        }
    ]


@given("a saved image-only message with a timestamp")
def saved_image_only_message_with_timestamp(timestamp_ctx: dict[str, Any]) -> None:
    timestamp_ctx["history"] = [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_image", "file_id": "file_123"}],
            "created_at": "2026-06-13T17:24:33+05:30",
        }
    ]


@when("the history is trimmed for OpenAI")
def trim_history_for_openai(timestamp_ctx: dict[str, Any]) -> None:
    timestamp_ctx["trimmed"] = gpt_utils.trim_input(timestamp_ctx["history"])


@when("the history is trimmed for OpenAI twice")
def trim_history_for_openai_twice(timestamp_ctx: dict[str, Any]) -> None:
    history = timestamp_ctx["history"]
    timestamp_ctx["original_history"] = [item.copy() for item in history]
    timestamp_ctx["first_trimmed"] = gpt_utils.trim_input(history)
    timestamp_ctx["second_trimmed"] = gpt_utils.trim_input(history)


@then("the timestamp is included in the text sent to OpenAI")
def timestamp_is_included_in_text(timestamp_ctx: dict[str, Any]) -> None:
    assert timestamp_ctx["trimmed"] == [
        {
            "type": "message",
            "role": "user",
            "content": "[Message sent at 2026-06-13T17:24:33+05:30]\nHi",
        }
    ]


@then("no timestamp text block is added to the image message")
def no_timestamp_block_is_added_to_image(timestamp_ctx: dict[str, Any]) -> None:
    assert timestamp_ctx["trimmed"] == [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_image", "file_id": "file_123"}],
        }
    ]


@then("both trimmed histories match without changing the saved message")
def trimmed_histories_match_without_mutating_history(
    timestamp_ctx: dict[str, Any],
) -> None:
    assert timestamp_ctx["first_trimmed"] == timestamp_ctx["second_trimmed"]
    assert timestamp_ctx["history"] == timestamp_ctx["original_history"]
    assert timestamp_ctx["first_trimmed"] == [
        {
            "type": "message",
            "role": "user",
            "content": "[Message sent at 2026-06-13T17:24:33+05:30]\nHi",
        }
    ]
