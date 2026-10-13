import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from faltoobot import claude_cli, claude_stream, claude_transcript, sessions

scenarios("features/claude_stream.feature")

RECORDINGS = Path(__file__).resolve().parent / "fakes" / "claude_stream"


@pytest.fixture
def ctx() -> dict[str, Any]:
    return {}


def _translate_recording(name: str) -> list[Any]:
    events: list[Any] = []
    block = None
    for line in (RECORDINGS / name).read_text(encoding="utf-8").splitlines():
        translated, block = claude_stream.translate(json.loads(line), block)
        events.extend(translated)
    return events


@when("I translate the recorded tool turn")
def translate_tool_turn(ctx: dict[str, Any]) -> None:
    ctx["events"] = _translate_recording("tool_turn.jsonl")


@when("I translate the recorded text turn")
def translate_text_turn(ctx: dict[str, Any]) -> None:
    ctx["events"] = _translate_recording("text_turn.jsonl")


def _of_type(ctx: dict[str, Any], kind: str) -> list[Any]:
    return [event for event in ctx["events"] if event.type == kind]


@then(
    parsers.parse(
        'the events include a run_shell_call item with the command "{command}"'
    )
)
def run_shell_call(ctx: dict[str, Any], command: str) -> None:
    items = [event.item for event in _of_type(ctx, "response.output_item.done")]
    assert items[0]["name"] == "run_shell_call"
    assert json.loads(items[0]["arguments"])["command"] == command


@then(parsers.parse('the events include a function_call_output with "{output}"'))
def function_call_output(ctx: dict[str, Any], output: str) -> None:
    assert [event.output for event in _of_type(ctx, "function_call_output")] == [output]


@then(parsers.parse('the streamed answer is "{text}"'))
def streamed_answer(ctx: dict[str, Any], text: str) -> None:
    deltas = _of_type(ctx, "response.output_text.delta")
    assert "".join(event.delta for event in deltas) == text


@then("a thinking block was opened")
def thinking_opened(ctx: dict[str, Any]) -> None:
    assert _of_type(ctx, "response.reasoning_summary_part.added")


@then("the five-hour rate limit is reported")
def rate_limit(ctx: dict[str, Any]) -> None:
    (limits,) = _of_type(ctx, "codex.rate_limits")
    assert "five_hour" in limits.rate_limits


@given(
    parsers.parse(
        'a pending history of user "{first}", developer "{note}", user "{second}"'
    )
)
def pending_history(ctx: dict[str, Any], first: str, note: str, second: str) -> None:
    ctx["messages"] = [
        {"type": "claude_turn"},
        {"type": "message", "role": "user", "content": first},
        sessions.developer_message_item(note),
        {"type": "message", "role": "user", "content": second},
    ]


@when("I render the prompt")
def render(ctx: dict[str, Any]) -> None:
    ctx["prompt"] = claude_cli.render_prompt(claude_cli.pending_items(ctx["messages"]))


@then(parsers.parse('the prompt contains "{first}" and "{second}"'))
def prompt_contains(ctx: dict[str, Any], first: str, second: str) -> None:
    assert first in ctx["prompt"] and second in ctx["prompt"]


@then("the developer note is wrapped in a system-reminder block")
def reminder(ctx: dict[str, Any]) -> None:
    assert "<system-reminder>\nclean up\n</system-reminder>" in ctx["prompt"]


@when("I reference an attachment inside the workspace and one outside it")
def attachments(ctx: dict[str, Any], tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / ".whatsapp").mkdir(parents=True)
    inside = workspace / ".whatsapp" / "photo.png"
    outside = tmp_path / "clipboard.png"
    for path in (inside, outside):
        path.write_bytes(b"png")
    ctx.update(workspace=workspace, outside=outside.resolve())
    ctx["texts"] = [
        item["text"]
        for item in claude_cli.attachment_items(workspace, [inside, outside])
    ]


@then("the inside one is referenced by its relative path")
def inside_relative(ctx: dict[str, Any]) -> None:
    assert ctx["texts"][0] == "Attached file: .whatsapp/photo.png"


@then("the outside one is referenced by its absolute path")
def outside_absolute(ctx: dict[str, Any]) -> None:
    assert ctx["texts"][1] == f"Attached file: {ctx['outside'].as_posix()}"


@then("nothing was copied into the workspace")
def nothing_copied(ctx: dict[str, Any]) -> None:
    files = [path.name for path in ctx["workspace"].rglob("*") if path.is_file()]
    assert files == ["photo.png"]


@given(
    "a Claude transcript with a skill body, a compaction summary, a slash command, an interrupt note, a task notification and a harness note"
)
def bookkeeping_transcript(
    ctx: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    # comment: lookups go by conversation id, whatever the project folder is called.
    project = tmp_path / ".claude" / "projects" / "-any-project"
    project.mkdir(parents=True)

    def user(text: str, **extra: Any) -> dict[str, Any]:
        return {"type": "user", "message": {"role": "user", "content": text}, **extra}

    lines = [
        user("<system-reminder>\nharness note\n</system-reminder>\n\nhello"),
        user("Base directory for this skill: /tmp/skill", isMeta=True),
        user("This session is being continued.", isCompactSummary=True),
        user("<command-name>/model</command-name>"),
        user("<local-command-stdout>Set model</local-command-stdout>"),
        user("[Request interrupted by user]"),
        user(
            "<task-notification>\n<summary>tests passed</summary>\n</task-notification>",
            origin={"kind": "task-notification"},
        ),
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "hi there"}],
            },
        },
    ]
    (project / "s1.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n"
    )


@when("I read the transcript")
def read_it(ctx: dict[str, Any]) -> None:
    ctx["items"] = claude_transcript.read_transcript("s1")


@then(
    parsers.parse(
        'the user turns are "{first}" and the background update for "{summary}"'
    )
)
def user_turns(ctx: dict[str, Any], first: str, summary: str) -> None:
    users = [item["content"] for item in ctx["items"] if item.get("role") == "user"]
    first_user, update = users
    assert first_user == first
    assert update.startswith("# Background update") and summary in update


@then(parsers.parse('the assistant answer "{text}" is kept'))
def assistant_kept(ctx: dict[str, Any], text: str) -> None:
    answers = [item for item in ctx["items"] if item.get("role") == "assistant"]
    assert answers[0]["content"][0]["text"] == text


@when(parsers.parse('Claude finishes its own turn with "{reply}" in a WhatsApp chat'))
def whatsapp_delivery(
    ctx: dict[str, Any], reply: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from faltoobot.whatsapp import app as whatsapp_app
    from faltoobot.whatsapp import runtime

    sent: list[str] = []

    async def fake_send_text(client: Any, *, chat: Any, text: str, **_: Any) -> None:
        sent.append(text)

    monkeypatch.setattr(runtime, "send_text", fake_send_text)
    monkeypatch.setattr(
        whatsapp_app, "get_messages", lambda session: {"workspace": str(tmp_path)}
    )

    async def events() -> Any:
        yield SimpleNamespace(
            type="response.completed", response=SimpleNamespace(output_text=reply)
        )

    session = sessions.Session(chat_key="15555550123@s.whatsapp.net", session_id="s")
    asyncio.run(whatsapp_app._deliver_claude_turn(session, events()))
    ctx["sent"] = sent


@then(parsers.parse('WhatsApp sent "{text}"'))
def whatsapp_sent(ctx: dict[str, Any], text: str) -> None:
    assert ctx["sent"] == [text]


@then("WhatsApp sent nothing")
def whatsapp_sent_nothing(ctx: dict[str, Any]) -> None:
    assert ctx["sent"] == []
