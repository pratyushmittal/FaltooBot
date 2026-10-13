import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from claude_support import CHAT_KEY, patch_claude
from faltoobot import claude_cli, gpt_utils, handoff, sessions

scenarios("features/handoff.feature")

CRON_LINE = f'0 8 * * * faltoobot notify "{CHAT_KEY}" "morning digest"'
SHELL_COMMAND = f"(crontab -l; echo '{CRON_LINE}') | crontab -"


@pytest.fixture
def ctx(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    patch_claude(monkeypatch, tmp_path)

    async def fake_openai(*, input: Any, **_: Any) -> Any:
        input.append(
            gpt_utils._to_message_item(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "OPENAI SUMMARY"}],
                }
            )
        )
        yield SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                output=[], output_text="OPENAI SUMMARY", usage=None
            ),
        )

    monkeypatch.setattr(sessions, "_get_streaming_reply", fake_openai)
    state: dict[str, Any] = {"results": []}
    yield state
    claude_cli.shutdown()


def _message(role: str, content: Any) -> dict[str, Any]:
    return {
        "type": "message",
        "role": role,
        "content": content,
        "timestamp": "2026-10-01T09:00:00+05:30",
    }


@given("a WhatsApp chat on OpenAI with an archived history and a live history")
def openai_chat(ctx: Any) -> None:
    session = sessions.get_session(CHAT_KEY, session_id="old-session")
    messages_json = sessions.get_messages(session)
    (session.session_dir / "messages.archive.0001.json").write_text(
        json.dumps({"messages": [_message("user", "archived hello")]}), encoding="utf-8"
    )
    messages_json["messages"] = [
        _message("user", "I like tea in the morning"),
        {
            "type": "function_call",
            "name": "run_shell_call",
            "call_id": "call_1",
            "arguments": json.dumps(
                {"command": SHELL_COMMAND, "command_summary": "add digest cron"}
            ),
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
        _message(
            "user",
            "# Background update\n\nsub-agent follow-up id: sub-123\n\n## message\ndone",
        ),
        _message(
            "user",
            [
                {"type": "input_text", "text": "look at this"},
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64,AAAABASE64",
                },
            ],
        ),
        _message("assistant", [{"type": "output_text", "text": "noted"}]),
    ]
    messages_json["message_ids"] = ["wa-1", "wa-2"]
    sessions.set_messages(session, messages_json)
    ctx["old"] = session
    ctx["old_bytes"] = session.messages_path.read_bytes()


def _hand_off(ctx: Any, to: str, dry_run: bool = False) -> None:
    async def run() -> Any:
        result = await handoff.hand_off(CHAT_KEY, to=to, dry_run=dry_run)
        await claude_cli.stop_all()
        return result

    ctx["results"].append(asyncio.run(run()))


@when(parsers.parse('I hand the chat off to "{to}"'))
def hand_off(ctx: Any, to: str) -> None:
    _hand_off(ctx, to)


@when(parsers.parse('I hand the chat off to "{to}" as a dry run'))
def hand_off_dry(ctx: Any, to: str) -> None:
    _hand_off(ctx, to, dry_run=True)


@when(parsers.parse('the WhatsApp user sends "{command}"'))
def whatsapp_command(ctx: Any, command: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from faltoobot.whatsapp import runtime

    replies: list[str] = []

    class FakeClient:
        async def reply_message(self, text: str, event: Any) -> None:
            replies.append(text)

    async def fake_send_text(client: Any, *, text: str, **_: Any) -> None:
        replies.append(text)

    monkeypatch.setattr(runtime, "send_text", fake_send_text)
    event = SimpleNamespace(
        Info=SimpleNamespace(MessageSource=SimpleNamespace(Chat=None))
    )

    async def run() -> None:
        await runtime._handle_handoff(
            cast(Any, FakeClient()),
            session=ctx["old"],
            event=cast(Any, event),
            args=command,
        )
        await claude_cli.stop_all()

    asyncio.run(run())
    ctx["replies"] = replies


def _document(ctx: Any) -> str:
    return ctx["results"][-1].document.read_text(encoding="utf-8")


@then("the hand-off document has the OpenAI summary")
def document_summary(ctx: Any) -> None:
    assert "OPENAI SUMMARY" in _document(ctx)


def _note(ctx: Any) -> str:
    messages = sessions.get_messages(handoff.current_session(CHAT_KEY))["messages"]
    return messages[0]["content"][0]["text"]


@then("the hand-off note lists the archive before the live history file")
def note_lists_openai_files(ctx: Any) -> None:
    note = _note(ctx)
    archive = ctx["old"].session_dir / "messages.archive.0001.json"
    assert note.index(str(archive)) < note.index(str(ctx["old"].messages_path))


@then("the hand-off note lists Claude's transcript")
def note_lists_claude_transcript(ctx: Any) -> None:
    note = _note(ctx)
    assert ".jsonl" in note and str(ctx["old"].messages_path) not in note


@then("the chat now uses a new Claude session that starts with the hand-off note")
def new_claude_session(ctx: Any) -> None:
    result = ctx["results"][-1]
    current = handoff.current_session(CHAT_KEY)
    assert current.session_id == result.session.session_id != ctx["old"].session_id
    messages_json = sessions.get_messages(current)
    assert messages_json["backend"] == "claude"
    assert messages_json["message_ids"] == ["wa-1", "wa-2"]
    note = messages_json["messages"][0]["content"][0]["text"]
    assert str(result.document) in note


@then("the previous session file is unchanged")
def old_unchanged(ctx: Any) -> None:
    assert ctx["old"].messages_path.read_bytes() == ctx["old_bytes"]


@then("the verification reply came from Claude")
def verification(ctx: Any) -> None:
    assert ctx["results"][-1].verification.startswith("echo:")
    assert "Read the hand-off above" in ctx["results"][-1].verification


@then("the hand-off document exists")
def document_exists(ctx: Any) -> None:
    result = ctx["results"][-1]
    assert result.document.is_file() and result.session is None


@then("the chat still uses the previous session")
def still_previous(ctx: Any) -> None:
    assert handoff.current_session(CHAT_KEY).session_id == ctx["old"].session_id


@then("the latest hand-off document has the Claude summary")
def claude_summary(ctx: Any) -> None:
    assert "FORK SUMMARY" in _document(ctx)


@then("the chat now uses an OpenAI session")
def openai_session(ctx: Any) -> None:
    current = handoff.current_session(CHAT_KEY)
    assert sessions.get_messages(current)["backend"] == "openai"
    assert ctx["results"][-1].verification == "OPENAI SUMMARY"


@then(parsers.parse('WhatsApp replied "{reply}"'))
def whatsapp_replied(ctx: Any, reply: str) -> None:
    assert ctx["replies"][0] == reply.replace("\\|", "|")
