"""Turn attribution, tested by feeding stream-json lines straight into the reader."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from faltoobot import claude_cli, sessions

scenarios("features/claude_turns.feature")


class FakeStdin:
    def __init__(self) -> None:
        self.lines: list[dict[str, Any]] = []

    def write(self, data: bytes) -> None:
        self.lines.append(json.loads(data))

    async def drain(self) -> None:
        return None


@pytest.fixture
def ctx() -> Any:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    state: dict[str, Any] = {"loop": loop, "handled": [], "turn": None}
    yield state
    claude_cli.set_handler(None)
    claude_cli._processes.clear()
    loop.close()


def _run(ctx: Any, coro: Any) -> Any:
    return ctx["loop"].run_until_complete(coro)


def _process(ctx: Any, *, handler: bool = True, idle: float = 600) -> None:
    session = sessions.Session(chat_key="15555550123@s.whatsapp.net", session_id="s")
    stdin = FakeStdin()
    process = SimpleNamespace(stdin=stdin, returncode=None)
    proc = claude_cli.ClaudeProcess(
        session=session, process=cast(Any, process), idle_timeout=idle
    )
    claude_cli._processes[claude_cli._key(session)] = proc
    ctx.update(proc=proc, stdin=stdin, session=session)
    if handler:

        async def collect(session: Any, events: Any) -> None:
            async for event in events:
                if event.type == "response.completed":
                    ctx["handled"].append(event.response.output_text)

        claude_cli.set_handler(collect)


def _send_prompt(ctx: Any, *, echoed: bool = True) -> None:
    ctx["turn"] = claude_cli._Turn(echoed=echoed)
    ctx["proc"].awaiting.append(ctx["turn"])


@given("a Claude process with a sent prompt")
def with_prompt(ctx: Any) -> None:
    _process(ctx)
    _send_prompt(ctx)


@given("a Claude process with a sent slash command")
def with_slash_command(ctx: Any) -> None:
    _process(ctx)
    _send_prompt(ctx, echoed=False)


@given("a Claude process with no sent prompt")
def without_prompt(ctx: Any) -> None:
    _process(ctx)


@given("a Claude process with no sent prompt and no handler")
def without_handler(ctx: Any) -> None:
    _process(ctx, handler=False)


@given("a Claude process with no sent prompt and an idle timeout of 0 seconds")
def with_zero_idle(ctx: Any) -> None:
    _process(ctx, idle=0)


def _line(step: str) -> dict[str, Any]:  # noqa: PLR0911
    text = step.split('"')[1] if '"' in step else ""
    match step.split(' "')[0].strip():
        case "echo":
            return {"type": "user", "isReplay": True, "message": {"content": "prompt"}}
        case "task notification echo":
            content = "<task-notification><summary>done</summary></task-notification>"
            return {"type": "user", "isReplay": True, "message": {"content": content}}
        case "task notification":
            return {"type": "system", "subtype": "task_notification"}
        case "text":
            delta = {"type": "text_delta", "text": text}
            return {
                "type": "stream_event",
                "event": {"type": "content_block_delta", "delta": delta},
            }
        case "sub-agent text":
            delta = {"type": "text_delta", "text": text}
            event = {"type": "content_block_delta", "delta": delta}
            return {
                "type": "stream_event",
                "event": event,
                "parent_tool_use_id": "toolu_1",
            }
        case "result":
            return {"type": "result", "subtype": "success", "result": text}
        case other if other.startswith("background tasks"):
            count = int(other.split()[-1])
            return {
                "type": "system",
                "subtype": "background_tasks_changed",
                "tasks": [{}] * count,
            }
    raise ValueError(step)


@when(parsers.parse("Claude sends: {steps}"))
def claude_sends(ctx: Any, steps: str) -> None:
    async def feed() -> None:
        for step in steps.split(", "):
            claude_cli._dispatch(ctx["proc"], _line(step))
        await asyncio.sleep(0.05)  # comment: let delivery tasks finish.

    _run(ctx, feed())


@when("I interrupt Claude's own turn")
def interrupt(ctx: Any) -> None:
    ctx["interrupted"] = _run(ctx, claude_cli.interrupt(ctx["session"]))


@then(parsers.re(r'the prompt\'s reply is "(?P<text>.*)"'))
def prompt_reply(ctx: Any, text: str) -> None:
    async def drain() -> str:
        answer = ""
        async for event in claude_cli._turn_events(ctx["turn"], timeout=1):
            if event.type == "response.completed":
                answer = event.response.output_text
            elif event.type == "response.output_text.delta":
                assert "LEAKED" not in event.delta
        return answer

    assert _run(ctx, drain()) == text


@then(parsers.re(r'the handler received "(?P<text>.*)"'))
def handler_received(ctx: Any, text: str) -> None:
    assert ctx["handled"] == [text]


@then("the handler received nothing")
def handler_nothing(ctx: Any) -> None:
    assert ctx["handled"] == []


@then(parsers.parse('the kept replies are "{text}"'))
def kept(ctx: Any, text: str) -> None:
    assert ctx["proc"].unclaimed == [text]


@then("the process is still running after the idle timeout")
def still_running(ctx: Any) -> None:
    _run(ctx, asyncio.sleep(0.05))
    assert claude_cli._processes.get(claude_cli._key(ctx["session"])) is ctx["proc"]


@then("the interrupt request was written to Claude")
def interrupt_written(ctx: Any) -> None:
    assert ctx["interrupted"] is True
    (request,) = ctx["stdin"].lines
    assert request["request"]["subtype"] == "interrupt"
