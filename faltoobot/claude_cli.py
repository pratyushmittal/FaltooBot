"""Claude CLI backend: one long-lived `claude -p --input-format stream-json` process per session.

Claude keeps its own transcript under ~/.claude; the harness stores only user turns and a
thin `claude_turn` marker per completed turn. A reader task follows each process for its
whole life, like the faltoo-emacs bridge: the turn after Claude echoes a user message
answers it, and a turn with no echo before it was started by Claude itself, e.g. when a
background task finished. Those go to the handler the owning client registered.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from faltoobot import claude_transcript
from faltoobot.claude_stream import content_text, event, translate, turn_usage
from faltoobot.config import Config
from faltoobot.instructions import get_system_instructions
from faltoobot.gpt_utils import (
    MessageHistory,
    MessageItem,
    StreamingReplyItem,
)
from faltoobot.skills import sync_claude_skills_plugin
from faltoobot.tools import _tool_env

if TYPE_CHECKING:
    from faltoobot.sessions import MessagesJson, Session

logger = logging.getLogger("faltoobot")

TURN_MARKER = "claude_turn"
PLUGIN_DIR = "claude-plugin"
SYSTEM_PROMPT_FILE = "claude-system-prompt.md"
INTERRUPT_TIMEOUT_SECONDS = 10.0
STOP_TIMEOUT_SECONDS = 10.0
# comment: Claude Code writes a whole tool result as one stdout line; asyncio's default is 64 KB.
LINE_LIMIT = 64 * 1024 * 1024
# comment: Claude replays these as user lines too, but they are not our prompts.
NOT_PROMPT_ECHOES = ("<task-notification>", "<local-command-stdout>")
HARNESS_NOTES = """
Harness notes:
- You are running inside FaltooBot. The chat key of this conversation is `{chat_key}`.
- The working directory `{workspace}` is this chat's workspace; keep files there.
- FaltooBot skills are installed as native skills under the `faltoobot:` prefix. Use the matching skill before tasks it covers.
- Text wrapped in <system-reminder> tags inside a user message comes from the harness, not from the user.
- Attachments arrive as `Attached file: <path>` lines; open them with the Read tool.
- Background work you start lives only as long as this chat's process; it ends when FaltooBot restarts. Use crontab and `faltoobot notify` for anything that must survive that.
""".strip()

Persist = Callable[[], None]
TurnEvents = AsyncIterator[StreamingReplyItem]
Handler = Callable[["Session", TurnEvents], Awaitable[None]]


class ClaudeCliError(RuntimeError):
    """The Claude CLI failed before finishing a turn."""


_END = object()


@dataclass
class _Turn:
    queue: asyncio.Queue[Any] = field(default_factory=asyncio.Queue)
    self_started: bool = False
    # comment: Claude neither echoes nor answers slash commands such as /compact in text.
    echoed: bool = True
    result: dict[str, Any] | None = None


@dataclass
class ClaudeProcess:
    session: Session
    process: asyncio.subprocess.Process
    idle_timeout: float
    stderr_tail: deque[str] = field(default_factory=lambda: deque(maxlen=50))
    stderr_task: asyncio.Task[None] | None = None
    reader_task: asyncio.Task[None] | None = None
    idle_task: asyncio.Task[None] | None = None
    awaiting: deque[_Turn] = field(default_factory=deque)
    current: _Turn | None = None
    block: str | None = None
    background_tasks: int = 0
    unclaimed: list[str] = field(default_factory=list)


_processes: dict[str, ClaudeProcess] = {}
_handlers: dict[str | None, Handler] = {}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _key(session: Session) -> str:
    return f"{session.chat_key}/{session.session_id}"


def set_handler(handler: Handler | None, *, chat_key: str | None = None) -> None:
    """Receive turns Claude starts by itself; `chat_key=None` covers every chat."""
    if handler is None:
        _handlers.pop(chat_key, None)
    else:
        _handlers[chat_key] = handler


# --- history helpers -----------------------------------------------------------


def pending_items(messages: MessageHistory) -> MessageHistory:
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("type") == TURN_MARKER:
            return messages[index + 1 :]
    return list(messages)


def render_prompt(items: MessageHistory) -> str:
    chunks: list[str] = []
    for item in items:
        if item.get("type") != "message":
            continue
        text = content_text(item.get("content")).strip()
        if not text:
            continue
        if item.get("role") == "developer":
            # comment: Claude Code's own convention for harness-originated notes.
            chunks.append(f"<system-reminder>\n{text}\n</system-reminder>")
        else:
            chunks.append(text)
    return "\n\n".join(chunks)


def attachment_items(
    workspace: Path, attachments: Sequence[str | Path]
) -> list[dict[str, Any]]:
    """Reference attachments where they are; Claude reads them with its own tools."""
    workspace = workspace.resolve()
    items: list[dict[str, Any]] = []
    for source in attachments:
        path = Path(source).expanduser().resolve()
        # comment: relative inside the workspace, absolute outside; no copies.
        shown = path.relative_to(workspace) if path.is_relative_to(workspace) else path
        items.append(
            {"type": "input_text", "text": f"Attached file: {shown.as_posix()}"}
        )
    return items


# --- the reader ----------------------------------------------------------------


async def _turn_events(turn: _Turn, timeout: float | None = None) -> TurnEvents:
    """Yield a turn's events and finish with `response.completed`."""
    while True:
        item = await asyncio.wait_for(turn.queue.get(), timeout)
        if item is _END:
            break
        if isinstance(item, BaseException):
            raise item
        yield item
    text = str((turn.result or {}).get("result") or "")
    yield event(
        "response.completed",
        response=SimpleNamespace(output=[], output_text=text, usage=None),
    )


async def _deliver(proc: ClaudeProcess, turn: _Turn) -> None:
    handler = _handlers.get(proc.session.chat_key) or _handlers.get(None)
    try:
        if handler is not None:
            await handler(proc.session, _turn_events(turn))
            return
        # comment: one-shot runs have no handler; finish_session() returns these.
        async for event in _turn_events(turn):
            if event.type == "response.completed":
                proc.unclaimed.append(event.response.output_text)  # type: ignore
    except Exception:
        logger.exception("Delivering a Claude-started turn failed")


def _end_turn(proc: ClaudeProcess, result: dict[str, Any] | None) -> None:
    turn, proc.current = proc.current, None
    proc.block = None
    if turn is not None:
        turn.result = result
        turn.queue.put_nowait(_END)
    _schedule_idle_stop(proc)


def _open_turn(proc: ClaudeProcess) -> _Turn:
    if proc.awaiting and not proc.awaiting[0].echoed:
        proc.current = proc.awaiting.popleft()
        return proc.current
    proc.current = turn = _Turn(self_started=True)
    asyncio.create_task(_deliver(proc, turn))
    return turn


def _on_echo(proc: ClaudeProcess, payload: dict[str, Any]) -> None:
    text = content_text(payload.get("message", {}).get("content")).strip()
    if text.startswith(NOT_PROMPT_ECHOES) or (
        proc.current is not None and not proc.current.echoed
    ):
        return
    if proc.current is not None:
        # comment: Claude took the prompt mid-turn; what came before was its own turn.
        _end_turn(proc, None)
    proc.current = proc.awaiting.popleft() if proc.awaiting else None


def _dispatch(proc: ClaudeProcess, payload: dict[str, Any]) -> None:
    kind, subtype = payload.get("type"), payload.get("subtype")
    if payload.get("parent_tool_use_id"):
        return
    if kind == "system":
        if subtype == "background_tasks_changed":
            proc.background_tasks = len(payload.get("tasks") or [])
            _schedule_idle_stop(proc)
        return
    if kind == "user" and payload.get("isReplay"):
        _on_echo(proc, payload)
        return
    if kind not in {"stream_event", "assistant", "user", "rate_limit_event", "result"}:
        return
    turn = proc.current or _open_turn(proc)
    if kind == "result":
        _end_turn(proc, payload)
        return
    events, proc.block = translate(payload, proc.block)
    for item in events:
        turn.queue.put_nowait(item)


async def _read_forever(proc: ClaudeProcess) -> None:
    stdout = proc.process.stdout
    assert stdout is not None
    error: BaseException | None = None
    try:
        while raw := await stdout.readline():
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                _dispatch(proc, payload)
    except (
        Exception
    ) as exc:  # comment: a dead reader must fail open turns, not hang them.
        error = exc
        proc.process.kill()
    code = await proc.process.wait()
    if proc.stderr_task is not None:
        # comment: stderr usually explains the exit; wait briefly for it to drain.
        await asyncio.wait([proc.stderr_task], timeout=2.0)
    detail = "\n".join(proc.stderr_tail).strip()
    error = error or ClaudeCliError(
        f"Claude CLI exited with code {code}." + (f"\n{detail}" if detail else "")
    )
    for turn in [proc.current, *proc.awaiting]:
        if turn is not None:
            turn.queue.put_nowait(error)
    proc.current = None
    proc.awaiting.clear()
    # comment: a pending idle timer would otherwise keep this dead process alive.
    _cancel_idle(proc)
    key = _key(proc.session)
    if _processes.get(key) is proc:
        _processes.pop(key)


async def _drain_stderr(process: asyncio.subprocess.Process, tail: deque[str]) -> None:
    assert process.stderr is not None
    while line := await process.stderr.readline():
        tail.append(line.decode(errors="replace").rstrip())


# --- process lifecycle ---------------------------------------------------------


def _shared_flags(config: Config) -> list[str]:
    if not shutil.which(config.claude_binary):
        raise ClaudeCliError(f"`{config.claude_binary}` CLI not found in PATH.")
    return [
        "--model",
        config.claude_model,
        "--effort",
        config.claude_effort,
        "--permission-mode",
        config.claude_permission_mode,
        "--permission-prompts",
        "none",
    ]


def _claude_env() -> dict[str, str]:
    env = _tool_env()
    # comment: a sub-agent's --claude-model must not leak into agents Claude starts itself.
    env.pop("FALTOOBOT_CLAUDE_MODEL", None)
    return env


def build_command(
    config: Config,
    *,
    session_id: str,
    system_prompt_file: Path,
    plugin_dir: Path | None,
) -> list[str]:
    command = [
        config.claude_binary,
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        # comment: Claude echoes each prompt when it takes it; that is how turns are told apart.
        "--replay-user-messages",
        *_shared_flags(config),
        "--append-system-prompt-file",
        str(system_prompt_file),
        # comment: AGENTS.md is the bot's memory; a restarted process must see the latest.
        "--system-prompt-snapshot",
        "off",
    ]
    if plugin_dir is not None:
        command.extend(["--plugin-dir", str(plugin_dir)])
    # comment: resume this session's own conversation once Claude has saved it.
    started = claude_transcript.transcript_path(session_id) is not None
    command.extend(["--resume" if started else "--session-id", session_id])
    return command


async def _start(
    session: Session, messages_json: MessagesJson, config: Config
) -> ClaudeProcess:
    workspace = Path(messages_json["workspace"])
    # comment: only a starting process reads the prompt, so build it only then.
    instructions = get_system_instructions(config, session.chat_key, workspace)
    notes = HARNESS_NOTES.format(chat_key=session.chat_key, workspace=workspace)
    prompt_file = session.session_dir / SYSTEM_PROMPT_FILE
    prompt_file.write_text(f"{instructions}\n\n{notes}\n", encoding="utf-8")
    plugin_dir = session.session_dir / PLUGIN_DIR
    synced = sync_claude_skills_plugin(plugin_dir, workspace, chat_key=session.chat_key)
    command = build_command(
        config,
        session_id=messages_json["claude_session_id"],
        system_prompt_file=prompt_file,
        plugin_dir=plugin_dir if synced else None,
    )
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=workspace,
        env=_claude_env(),
        start_new_session=True,
        limit=LINE_LIMIT,
    )
    proc = ClaudeProcess(
        session=session,
        process=process,
        idle_timeout=float(config.claude_idle_timeout),
    )
    proc.stderr_task = asyncio.create_task(_drain_stderr(process, proc.stderr_tail))
    proc.reader_task = asyncio.create_task(_read_forever(proc))
    logger.info("Started claude for %s (pid %s)", _key(session), process.pid)
    return proc


async def _get_process(
    session: Session, messages_json: MessagesJson, config: Config
) -> ClaudeProcess:
    key = _key(session)
    proc = _processes.get(key)
    if proc is None or proc.process.returncode is not None:
        # comment: config and prompt changes apply when the process next starts.
        proc = _processes[key] = await _start(session, messages_json, config)
    _cancel_idle(proc)
    return proc


def _cancel_idle(proc: ClaudeProcess) -> None:
    # comment: the idle task calls _stop itself and must not cancel its own wait.
    if proc.idle_task is not None and proc.idle_task is not asyncio.current_task():
        proc.idle_task.cancel()
    proc.idle_task = None


async def _stop_when_idle(proc: ClaudeProcess) -> None:
    await asyncio.sleep(proc.idle_timeout)
    key = _key(proc.session)
    busy = proc.current or proc.awaiting or proc.background_tasks
    # comment: background work ends with the process, so never stop while it runs.
    if not busy and _processes.get(key) is proc:
        await _stop(key)


def _schedule_idle_stop(proc: ClaudeProcess) -> None:
    _cancel_idle(proc)
    proc.idle_task = asyncio.create_task(_stop_when_idle(proc))


async def _stop(key: str) -> None:
    proc = _processes.pop(key, None)
    if proc is None:
        return
    _cancel_idle(proc)
    process = proc.process
    if process.returncode is None:
        if process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), STOP_TIMEOUT_SECONDS)
        except TimeoutError:
            process.kill()
            await process.wait()
    if proc.reader_task is not None:
        await proc.reader_task
    logger.info("Stopped claude for %s", key)


async def stop(session: Session) -> None:
    await _stop(_key(session))


async def ask_fork(
    workspace: Path, config: Config, session_id: str, prompt: str
) -> str:
    """Answer PROMPT in a throwaway fork of the session's conversation.

    The fork is never saved, so asking for a hand-off summary leaves the real
    conversation untouched, like the faltoo-emacs /btw command.
    """
    process = await asyncio.create_subprocess_exec(
        config.claude_binary,
        "-p",
        "--resume",
        session_id,
        "--fork-session",
        "--no-session-persistence",
        "--output-format",
        "json",
        *_shared_flags(config),
        prompt,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=workspace,
        env=_claude_env(),
        limit=LINE_LIMIT,
    )
    stdout, stderr = await process.communicate()
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError as exc:
        detail = stderr.decode(errors="replace").strip()
        raise ClaudeCliError(f"Claude CLI fork failed. {detail}") from exc
    _raise_for_result(result)
    return str(result.get("result") or "")


async def stop_all() -> None:
    for key in list(_processes):
        await _stop(key)


def shutdown() -> None:
    """Kill every process without awaiting; for teardown outside the owning loop."""
    for proc in _processes.values():
        if proc.process.returncode is None:
            proc.process.kill()
    _processes.clear()


async def finish_session(session: Session) -> list[str]:
    """Close a one-shot session's stdin, wait for Claude to exit, return turns it started."""
    proc = _processes.pop(_key(session), None)
    if proc is None:
        return []
    _cancel_idle(proc)
    if proc.process.stdin is not None and not proc.process.stdin.is_closing():
        # comment: Claude CLI 2.1.292+ waits for background work, then takes one more turn.
        proc.process.stdin.close()
    if proc.reader_task is not None:
        await proc.reader_task
    await asyncio.sleep(0)  # comment: let the last delivery task record its reply.
    return proc.unclaimed


async def interrupt(session: Session) -> bool:
    """Interrupt a turn Claude started by itself; returns whether one was running."""
    proc = _processes.get(_key(session))
    if proc is None or proc.current is None or not proc.current.self_started:
        return False
    await _send(proc, _interrupt_request())
    return True


# --- turns ---------------------------------------------------------------------


def _interrupt_request() -> dict[str, Any]:
    return {
        "type": "control_request",
        "request_id": uuid4().hex,
        "request": {"subtype": "interrupt"},
    }


async def _send(proc: ClaudeProcess, payload: dict[str, Any]) -> None:
    assert proc.process.stdin is not None
    proc.process.stdin.write(json.dumps(payload).encode() + b"\n")
    await proc.process.stdin.drain()


def _append_marker(
    messages: MessageHistory, result: dict[str, Any] | None, *, interrupted: bool
) -> None:
    marker: MessageItem = {"type": TURN_MARKER, "timestamp": _now()}
    if result is not None:
        marker["usage"] = turn_usage(result.get("usage"))
        marker["cost_usd"] = result.get("total_cost_usd")
    if interrupted:
        marker["interrupted"] = True
    messages.append(marker)


async def _interrupt(proc: ClaudeProcess, turn: _Turn) -> None:
    try:
        await _send(proc, _interrupt_request())
        async with asyncio.timeout(INTERRUPT_TIMEOUT_SECONDS):
            async for _ in _turn_events(turn):
                pass
    except (ClaudeCliError, TimeoutError, OSError):
        # comment: a process that cannot be interrupted is restarted on the next turn.
        proc.process.kill()


def _raise_for_result(result: dict[str, Any]) -> None:
    if not result.get("is_error") and result.get("subtype") == "success":
        return
    errors = result.get("errors")
    detail = str(result.get("result") or "") or (
        "; ".join(map(str, errors)) if isinstance(errors, list) else ""
    )
    raise ClaudeCliError(
        f"Claude CLI turn failed ({result.get('subtype')})."
        + (f" {detail}" if detail else "")
    )


async def _run_turn(  # noqa: PLR0913
    session: Session,
    messages_json: MessagesJson,
    *,
    prompt: str,
    config: Config,
    persist: Persist | None = None,
    echoed: bool = True,
) -> TurnEvents:
    """Send PROMPT and yield its events; prompt turns end with a turn marker.

    Slash commands (`echoed=False`) leave no marker, so items still pending stay
    pending for the next prompt.
    """

    def record(result: dict[str, Any] | None, *, interrupted: bool) -> None:
        if echoed:
            _append_marker(messages_json["messages"], result, interrupted=interrupted)
            if persist:
                persist()

    if not prompt:
        # comment: an earlier turn already answered everything, e.g. a WhatsApp notification.
        logger.info("Nothing pending for Claude in %s", session.chat_key)
        yield event(
            "response.completed",
            response=SimpleNamespace(output=[], output_text="", usage=None),
        )
        return
    # comment: callers run one turn per session at a time (WhatsApp chat lock, TUI queue).
    proc = await _get_process(session, messages_json, config)
    turn = _Turn(echoed=echoed)
    proc.awaiting.append(turn)
    try:
        await _send(
            proc, {"type": "user", "message": {"role": "user", "content": prompt}}
        )
        # comment: no idle timeout; Claude may run a silent tool for a long time.
        async for item in _turn_events(turn):
            if item.type == "response.completed":
                completed = item
                break
            yield item
    except (asyncio.CancelledError, GeneratorExit):
        # comment: GeneratorExit when the consumer was cancelled between two events.
        await _interrupt(proc, turn)
        record(turn.result, interrupted=True)
        raise
    finally:
        _schedule_idle_stop(proc)
    # comment: once Claude answered, even with an error, it has seen the prompt.
    record(turn.result, interrupted=False)
    _raise_for_result(turn.result or {})
    yield completed


def stream_reply(
    session: Session,
    messages_json: MessagesJson,
    *,
    config: Config,
    persist: Persist,
) -> TurnEvents:
    return _run_turn(
        session,
        messages_json,
        prompt=render_prompt(pending_items(messages_json["messages"])),
        config=config,
        persist=persist,
    )


async def compact(
    session: Session, messages_json: MessagesJson, config: Config
) -> bool:
    """Run Claude's own /compact; it leaves pending items for the next prompt."""
    # comment: Claude neither echoes nor answers /compact in text.
    async for _ in _run_turn(
        session, messages_json, prompt="/compact", config=config, echoed=False
    ):
        pass
    return True
