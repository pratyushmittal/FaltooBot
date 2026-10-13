import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from faltoobot import claude_cli, claude_transcript, sessions
from faltoobot.config import Config, build_config

pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(
        shutil.which("claude") is None, reason="claude CLI is not installed."
    ),
]

scenarios("features/claude_bridge_external.feature")

CHAT_KEY = "15555550123@s.whatsapp.net"


@pytest.fixture
def ctx(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    root = tmp_path / ".faltoobot"
    monkeypatch.setattr(sessions, "app_root", lambda: root)
    real = build_config()
    config = SimpleNamespace(**{name: getattr(real, name) for name in real.__slots__})
    config.root = root
    config.claude_binary = "claude"
    config.claude_model = "haiku"
    config.claude_effort = "low"
    config.claude_idle_timeout = 600
    monkeypatch.setattr(sessions, "build_config", lambda: cast(Config, config))
    state: dict[str, Any] = {"replies": [], "events": []}
    yield state
    claude_cli.shutdown()


@given("a real Claude session on haiku")
def real_session(ctx: Any) -> None:
    session = sessions.get_session(CHAT_KEY)
    messages_json = sessions.get_messages(session)
    messages_json["backend"] = "claude"
    sessions.set_messages(session, messages_json)
    ctx["session"] = session


@when(
    parsers.parse(
        'I ask it to remember "{word}", then ask which word, then ask it to echo "{echo}"'
    )
)
def three_turns(ctx: Any, word: str, echo: str) -> None:
    session = ctx["session"]
    questions = [
        f"Remember the word {word}. Reply exactly: ok",
        "Which word did I ask you to remember? Reply with that one word only.",
        f"Run `echo {echo}` with the Bash tool and reply exactly with its output.",
    ]

    async def run() -> None:
        for question in questions:
            await sessions.append_user_turn(session, question=question)
            events = [event async for event in sessions.get_answer_streaming(session)]
            ctx["events"].append(events)
            completed = next(e for e in events if e.type == "response.completed")
            ctx["replies"].append(completed.response.output_text)
        await claude_cli.stop_all()

    asyncio.run(run())


@then(parsers.parse('the second reply mentions "{text}"'))
def second_reply(ctx: Any, text: str) -> None:
    assert text in ctx["replies"][1].lower()


@then(parsers.parse('the third reply mentions "{text}"'))
def third_reply(ctx: Any, text: str) -> None:
    assert text in ctx["replies"][2]


@then(parsers.parse('the third turn reported a run_shell_call with "{command}"'))
def third_turn_tool(ctx: Any, command: str) -> None:
    items = [
        getattr(event, "item", None)
        for event in ctx["events"][2]
        if event.type == "response.output_item.done"
    ]
    commands = [
        json.loads(item["arguments"]).get("command")
        for item in items
        if item and item["name"] == "run_shell_call"
    ]
    assert command in commands


@then(parsers.parse("the history holds only user turns and {count:d} turn markers"))
def history_shape(ctx: Any, count: int) -> None:
    messages = sessions.get_messages(ctx["session"])["messages"]
    kinds = [(item.get("type"), item.get("role")) for item in messages]
    assert all(kind in {("message", "user"), ("claude_turn", None)} for kind in kinds)
    assert kinds.count(("claude_turn", None)) == count


@then("the transcript reader finds the conversation")
def transcript_found(ctx: Any) -> None:
    session_id = sessions.get_messages(ctx["session"])["claude_session_id"]
    items = claude_transcript.read_transcript(session_id)
    assert any(item.get("role") == "assistant" for item in items)
    assert any(item.get("type") == "function_call" for item in items)


@given("a real Claude session on haiku with a background-turn handler")
def real_session_with_handler(ctx: Any) -> None:
    real_session(ctx)
    ctx["handled"] = []

    async def handler(session: Any, events: Any) -> None:
        async for event in events:
            if event.type == "response.completed":
                ctx["handled"].append(event.response.output_text)

    claude_cli.set_handler(handler)


@when(
    parsers.parse(
        'I ask it to run "{command}" in the background and wait for its own turn'
    )
)
def run_background(ctx: Any, command: str) -> None:
    ctx["loop"] = asyncio.new_event_loop()

    async def run() -> None:
        session = ctx["session"]
        await sessions.append_user_turn(
            session,
            question=f"Start `{command}` with the Bash tool using run_in_background=true. Do not wait for it. Reply exactly: started",
        )
        ctx["replies"].append(await sessions.get_answer(session))
        for _ in range(120):
            if ctx["handled"]:
                return
            await asyncio.sleep(0.5)

    ctx["loop"].run_until_complete(run())


@when(parsers.parse('I ask it to reply exactly "{text}"'))
def reply_exactly(ctx: Any, text: str) -> None:
    async def run() -> None:
        session = ctx["session"]
        await sessions.append_user_turn(session, question=f"Reply exactly: {text}")
        ctx["replies"].append(await sessions.get_answer(session))
        await claude_cli.stop_all()

    ctx["loop"].run_until_complete(run())
    ctx["loop"].close()


@then("the handler received a reply")
def handler_got_reply(ctx: Any) -> None:
    assert ctx["handled"] and ctx["handled"][0].strip()


@then(parsers.parse('the last reply mentions "{text}"'))
def last_reply(ctx: Any, text: str) -> None:
    assert text in ctx["replies"][-1].lower()
