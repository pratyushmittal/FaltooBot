"""How FaltooBot drives a claude process, against the minimal fake in tests/fakes/claude."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from claude_support import CHAT_KEY, INSTRUCTIONS, claude_session, patch_claude
from faltoobot import claude_cli, sessions

scenarios("features/claude_process.feature")


@pytest.fixture
def ctx(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    config = patch_claude(monkeypatch, tmp_path)
    state: dict[str, Any] = {"config": config, "log": tmp_path / "claude-log.jsonl"}
    yield state
    claude_cli.shutdown()


def _starts(ctx: Any) -> list[dict[str, Any]]:
    log: Path = ctx["log"]
    return (
        [json.loads(line) for line in log.read_text().splitlines()]
        if log.exists()
        else []
    )


async def _ask(ctx: Any, question: str) -> str:
    await sessions.append_user_turn(ctx["session"], question=question)
    return await sessions.get_answer(ctx["session"])


def _run(ctx: Any, steps: Any) -> None:
    async def run() -> None:
        try:
            await steps()
        finally:
            await claude_cli.stop_all()

    asyncio.run(run())


@given("a Claude session")
def a_session(ctx: Any) -> None:
    ctx["session"] = claude_session()


@given("a Claude session with an idle timeout of 0 seconds")
def a_session_idle(ctx: Any) -> None:
    a_session(ctx)
    ctx["config"].claude_idle_timeout = 0


@given(parsers.parse('a workspace skill "{name}" exists'))
def workspace_skill(ctx: Any, name: str) -> None:
    workspace = Path(sessions.get_messages(ctx["session"])["workspace"])
    skill = workspace / ".skills" / f"{name}.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text("---\ndescription: temporary\n---\nDo the thing.\n")
    ctx["workspace_skill"] = skill


@when(parsers.parse('I ask "{first}" and then "{second}"'))
def ask_two(ctx: Any, first: str, second: str) -> None:
    async def steps() -> None:
        ctx["replies"] = [await _ask(ctx, first)]
        if ctx["config"].claude_idle_timeout == 0:
            await asyncio.sleep(0.3)  # comment: let the idle stop close the process.
        ctx["replies"].append(await _ask(ctx, second))

    _run(ctx, steps)


@when(parsers.parse('I cancel a "{slow}" turn and then ask "{question}"'))
def cancel_then_ask(ctx: Any, slow: str, question: str) -> None:
    async def steps() -> None:
        await sessions.append_user_turn(ctx["session"], question=slow)

        async def consume() -> None:
            async for _ in sessions.get_answer_streaming(ctx["session"]):
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        ctx["replies"] = [await _ask(ctx, question)]

    _run(ctx, steps)


@when(
    parsers.parse(
        'I ask "{first}", then "{crash}", then "{last}", tolerating the crash'
    )
)
def ask_with_crash(ctx: Any, first: str, crash: str, last: str) -> None:
    async def steps() -> None:
        await _ask(ctx, first)
        try:
            await _ask(ctx, crash)
        except claude_cli.ClaudeCliError as exc:
            ctx["error"] = str(exc)
        ctx["replies"] = [await _ask(ctx, last)]

    _run(ctx, steps)


@when(parsers.parse('I ask "{first}", compact, and ask "{second}"'))
def ask_compact_ask(ctx: Any, first: str, second: str) -> None:
    async def steps() -> None:
        ctx["replies"] = [await _ask(ctx, first)]
        ctx["compacted"] = await sessions.compact_message_history(ctx["session"])
        ctx["replies"].append(await _ask(ctx, second))

    _run(ctx, steps)


@when(
    parsers.parse(
        'I ask "{first}", the process stops, the workspace skill "{name}" is removed and I ask "{second}"'
    )
)
def ask_remove_skill_ask(ctx: Any, first: str, name: str, second: str) -> None:
    skills_dir = ctx["session"].session_dir / claude_cli.PLUGIN_DIR / "skills"
    ctx["skills_dir"] = skills_dir

    async def steps() -> None:
        await _ask(ctx, first)
        ctx["mtime"] = (
            (skills_dir / "background-notifications" / "SKILL.md").stat().st_mtime_ns
        )
        assert (skills_dir / name / "SKILL.md").is_file()
        await claude_cli.stop_all()
        ctx["workspace_skill"].unlink()
        await _ask(ctx, second)

    _run(ctx, steps)


@when(parsers.parse('I ask "{question}" and finish the session'))
def ask_and_finish(ctx: Any, question: str) -> None:
    async def steps() -> None:
        await _ask(ctx, question)
        ctx["finished"] = await claude_cli.finish_session(ctx["session"])

    _run(ctx, steps)


@then(parsers.parse('the replies are "{first}" and "{second}"'))
def replies(ctx: Any, first: str, second: str) -> None:
    assert ctx["replies"] == [first, second]


@then(parsers.parse('the last reply is "{text}"'))
def last_reply(ctx: Any, text: str) -> None:
    assert ctx["replies"][-1] == text


@then(parsers.parse('the last reply contains "{first}" and "{second}"'))
def last_reply_contains(ctx: Any, first: str, second: str) -> None:
    # comment: a failed turn leaves its message pending, so the retry re-sends it.
    assert first in ctx["replies"][-1] and second in ctx["replies"][-1]


def _flag(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


@then("claude was started once, with a new conversation id")
def started_once(ctx: Any) -> None:
    (start,) = _starts(ctx)
    session_id = sessions.get_messages(ctx["session"])["claude_session_id"]
    assert _flag(start["argv"], "--session-id") == session_id
    assert "--resume" not in start["argv"]


@then("claude was started twice, the second time resuming the same conversation")
def started_twice(ctx: Any) -> None:
    first, second = _starts(ctx)
    session_id = _flag(first["argv"], "--session-id")
    assert session_id and _flag(second["argv"], "--resume") == session_id


@then("claude was started with --replay-user-messages and no disallowed tools")
def flags(ctx: Any) -> None:
    argv = _starts(ctx)[0]["argv"]
    assert "--replay-user-messages" in argv and "--disallowedTools" not in argv


@then(parsers.parse("the history holds only user turns and {count:d} turn markers"))
def history_shape(ctx: Any, count: int) -> None:
    kinds = [
        (item.get("type"), item.get("role"))
        for item in sessions.get_messages(ctx["session"])["messages"]
    ]
    assert set(kinds) <= {("message", "user"), ("claude_turn", None)}
    assert kinds.count(("claude_turn", None)) == count


@then("the last usage reports total tokens")
def last_usage(ctx: Any) -> None:
    usage = sessions.get_last_usage(ctx["session"])
    assert usage is not None and usage["total_tokens"] > 0


@then("the displayed history includes Claude's answer from its transcript")
def displayed(ctx: Any) -> None:
    items = sessions.get_messages_or_claude_transcript(ctx["session"])
    assert any(item.get("role") == "assistant" for item in items)
    assert (
        sessions.get_messages_path_or_claude_transcript(ctx["session"]).suffix
        == ".jsonl"
    )


@then(
    "the system prompt Claude received contains the chat instructions and the chat key"
)
def system_prompt(ctx: Any) -> None:
    prompt = _starts(ctx)[0]["system_prompt"]
    assert INSTRUCTIONS in prompt and CHAT_KEY in prompt


@then(
    parsers.parse('the rendered skills include "{name}" with the chat key substituted')
)
def skills_rendered(ctx: Any, name: str) -> None:
    skill = _starts(ctx)[0]["skills"][name]
    assert CHAT_KEY in skill and "{chat_key}" not in skill
    # comment: frontmatter values are quoted so ': ' or '#' in a description stay valid YAML.
    assert f'name: "{name}"' in skill


@then("the history has an interrupted turn marker")
def interrupted_marker(ctx: Any) -> None:
    markers = [
        item
        for item in sessions.get_messages(ctx["session"])["messages"]
        if item.get("type") == "claude_turn"
    ]
    assert markers[0].get("interrupted") is True


@then(parsers.parse('the crash error mentions "{text}"'))
def crash_error(ctx: Any, text: str) -> None:
    assert text in ctx["error"]


@then("compaction succeeded")
def compacted(ctx: Any) -> None:
    assert ctx["compacted"] is True


@then("the bundled skill files were not rewritten on restart")
def skills_not_rewritten(ctx: Any) -> None:
    skill = ctx["skills_dir"] / "background-notifications" / "SKILL.md"
    assert skill.stat().st_mtime_ns == ctx["mtime"]


@then(parsers.parse('the rendered skill "{name}" is gone'))
def skill_gone(ctx: Any, name: str) -> None:
    assert not (ctx["skills_dir"] / name).exists()


@then("no Claude process is left running")
def none_running(ctx: Any) -> None:
    assert ctx["finished"] == [] and not claude_cli._processes


@when(
    parsers.parse('I ask "{failing}", tolerating the error, and then ask "{question}"')
)
def ask_failing(ctx: Any, failing: str, question: str) -> None:
    async def steps() -> None:
        with pytest.raises(claude_cli.ClaudeCliError):
            await _ask(ctx, failing)
        ctx["replies"] = [await _ask(ctx, question)]

    _run(ctx, steps)


@when(parsers.parse('I cancel a "{slow}" turn the way the TUI does'))
def cancel_like_tui(ctx: Any, slow: str) -> None:
    async def steps() -> None:
        await sessions.append_user_turn(ctx["session"], question=slow)

        async def consume() -> None:
            async for _ in sessions.get_answer_streaming(ctx["session"]):
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(1.0)
        # comment: the TUI cancels and records the note right away, before the turn winds down.
        task.cancel()
        sessions.record_interrupted_response(ctx["session"])
        with pytest.raises(asyncio.CancelledError):
            await task

    _run(ctx, steps)


@then("the history ends with the interrupted note")
def interrupted_note(ctx: Any) -> None:
    last = sessions.get_messages(ctx["session"])["messages"][-1]
    assert last["role"] == "developer"
    assert last["content"][0]["text"] == sessions.INTERRUPTED_RESPONSE_INSTRUCTION


@when(
    parsers.parse(
        'Claude has answered "{first}" and a developer note "{note}" is pending'
    )
)
def answered_with_note(ctx: Any, first: str, note: str) -> None:
    async def steps() -> None:
        await _ask(ctx, first)
        sessions.append_developer_message(ctx["session"], note)

    _run(ctx, steps)


@when(parsers.parse('I compact and then ask "{question}"'))
def compact_then_ask(ctx: Any, question: str) -> None:
    async def steps() -> None:
        await sessions.compact_message_history(ctx["session"])
        ctx["replies"] = [await _ask(ctx, question)]

    _run(ctx, steps)


@when(
    parsers.parse(
        'I ask "{first}", another Claude run in the workspace writes a newer conversation, and I ask "{second}"'
    )
)
def other_conversation(ctx: Any, first: str, second: str) -> None:
    async def steps() -> None:
        await _ask(ctx, first)
        await claude_cli.stop_all()
        # comment: e.g. a sub-agent started in the chat's own workspace.
        own = sessions.get_messages_path_or_claude_transcript(ctx["session"])
        other = own.with_name("someone-else.jsonl")
        other.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "OTHER"}]},
                }
            )
            + "\n"
        )
        ctx["replies"] = [await _ask(ctx, second)]

    _run(ctx, steps)


@then(
    parsers.parse('the displayed history shows "{text}" and not the other conversation')
)
def own_history(ctx: Any, text: str) -> None:
    items = sessions.get_messages_or_claude_transcript(ctx["session"])
    answers = [
        item["content"][0]["text"] for item in items if item.get("role") == "assistant"
    ]
    assert text in answers and "OTHER" not in answers


@when(
    parsers.parse('I ask "{question}" and then ask for an answer with nothing pending')
)
def ask_with_nothing_pending(ctx: Any, question: str) -> None:
    async def steps() -> None:
        ctx["replies"] = [await _ask(ctx, question)]
        # comment: an earlier turn already answered everything, e.g. a WhatsApp notification.
        ctx["replies"].append(await sessions.get_answer(ctx["session"]))

    _run(ctx, steps)


@then("the last reply is empty")
def last_reply_empty(ctx: Any) -> None:
    assert ctx["replies"][-1] == ""
