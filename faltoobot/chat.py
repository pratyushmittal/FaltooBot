import argparse
import asyncio
import json
import os
import re
import select
import subprocess
import sys
import termios
import time
import tty
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
from rich.console import Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Button, Static, TextArea
from textual.widgets import Markdown as TextualMarkdown
from textual.widgets.markdown import MarkdownFence

from faltoobot.agent import stream_reply
from faltoobot.config import Config, build_config
from faltoobot.store import (
    QueuedPrompt,
    Session,
    Turn,
    add_turn,
    cli_session,
    existing_cli_session,
    replace_queued_prompts,
    session_items,
)

MARKDOWN_KINDS = frozenset({"bot", "thinking"})
TURN_KIND = {"user": "you", "assistant": "bot"}
MAX_TOOL_LINES = 8


MarkdownFence.highlight = classmethod(lambda cls, code, language: Content(code))  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class Entry:
    kind: str
    content: str


@dataclass(slots=True)
class StreamState:
    active_kind: str | None = None
    saw_bot: bool = False
    saw_thinking: bool = False
    tool_keys: set[str] = field(default_factory=set)


def default_session_name() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def help_text() -> str:
    return "Commands: /help, /tree, /reset, /exit"


def session_name(name: str | None) -> str:
    return f"CLI {name or default_session_name()}"


def status_text(config: Config) -> str:
    return f"model: {config.openai_model}  thinking: {config.openai_thinking}"


def _channel_value(value: str) -> int:
    if len(value) == 2:
        return int(value, 16)
    if len(value) == 4:
        return int(value[:2], 16)
    return int(value[:2], 16)


def terminal_background_dark(timeout: float = 0.1) -> bool | None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return None
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        os.write(sys.stdout.fileno(), b"\x1b]11;?\x07")
        end = time.monotonic() + timeout
        data = bytearray()
        while time.monotonic() < end:
            ready, _, _ = select.select([fd], [], [], end - time.monotonic())
            if not ready:
                break
            data.extend(os.read(fd, 256))
            if b"\x07" in data or b"\x1b\\" in data:
                break
    except OSError:
        return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

    match = re.search(
        rb"11;rgb:([0-9a-fA-F]{2,4})/([0-9a-fA-F]{2,4})/([0-9a-fA-F]{2,4})",
        bytes(data),
    )
    if match is None:
        return None
    red, green, blue = (_channel_value(value.decode()) for value in match.groups())
    brightness = (0.299 * red + 0.587 * green + 0.114 * blue) / 255
    return brightness < 0.5


def input_hint(
    config: Config,
    *,
    replying: bool = False,
    queued: int = 0,
    queue_selected: bool = False,
) -> str:
    parts = [status_text(config), "Enter send", "Shift+Enter newline", "Ctrl+C interrupt"]
    if replying:
        parts.append("replying")
    if queued:
        parts.append(f"queued {queued}")
    if queue_selected:
        parts.append("Ctrl+E edit")
        parts.append("Ctrl+P pause")
        parts.append("Del remove")
        parts.append("Alt+Up/Down reorder")
    return "  ".join(parts)


def open_in_default_editor(path: Path) -> None:
    command = ["open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
    subprocess.Popen(command)  # noqa: S603


def tool_lines(item: dict[str, Any]) -> list[str]:
    item_type = item.get("type")
    if not isinstance(item_type, str):
        return []
    if item_type == "shell_call":
        action = item.get("action")
        commands = action.get("commands") if isinstance(action, dict) else None
        if isinstance(commands, list):
            return ["shell", *(str(command) for command in commands)]
    if item_type in {"local_shell_call", "function_shell_call"}:
        action = item.get("action")
        command = action.get("command") if isinstance(action, dict) else None
        if isinstance(command, list):
            return ["shell", " ".join(str(part) for part in command)]
    if item_type == "function_call":
        name = item.get("name")
        arguments = item.get("arguments")
        if isinstance(name, str):
            lines = [name]
            if isinstance(arguments, str) and arguments.strip():
                try:
                    payload = json.dumps(json.loads(arguments), ensure_ascii=False, indent=2)
                except json.JSONDecodeError:
                    payload = arguments
                lines.extend(payload.splitlines())
            return lines
    if item_type in {"web_search_call", "function_web_search", "tool_search_call", "file_search_call"}:
        action = item.get("action")
        query = action.get("query") if isinstance(action, dict) else item.get("query")
        if isinstance(query, str) and query.strip():
            return ["web search", query]
        return ["web search"]
    if item_type.endswith("_call") and not item_type.endswith("_output"):
        details = item.get("name") or item.get("call_id") or item.get("id")
        label = item_type.replace("_", " ")
        return [label, str(details)] if details else [label]
    return []


def tool_entry(item: dict[str, Any]) -> str | None:
    lines = tool_lines(item)
    if not lines:
        return None
    clipped = lines[:MAX_TOOL_LINES]
    if len(lines) > MAX_TOOL_LINES:
        clipped[-1] = "..."
    return "\n".join(clipped)


def entry_class(kind: str) -> str:
    return f"entry-{kind}"


def item_entries(item: dict[str, Any]) -> list[Entry]:
    if item.get("type") == "reasoning":
        return [
            Entry("thinking", text)
            for summary in item.get("summary", [])
            if isinstance(summary, dict)
            for text in [summary.get("text")]
            if isinstance(text, str) and text.strip()
        ]
    if text := tool_entry(item):
        return [Entry("tool", text)]
    return []


def item_id(item: dict[str, Any]) -> str | None:
    value = item.get("id") or item.get("call_id")
    return value if isinstance(value, str) else None


def item_key(item: dict[str, Any]) -> str | None:
    return item_id(item) or tool_entry(item)


def turn_entries(turn: Turn) -> list[Entry]:
    return [
        *(entry for item in turn.items for entry in item_entries(item)),
        Entry(TURN_KIND.get(turn.role, "bot"), turn.content),
    ]


def history_entries(session: Session) -> list[Entry]:
    return [entry for turn in session.messages for entry in turn_entries(turn)]


def looks_like_markdown(content: str) -> bool:
    return any(token in content for token in ("**", "__", "`", "[", "](", "\n#", "\n-", "\n1. "))


def uses_markdown(kind: str, content: str) -> bool:
    return kind in MARKDOWN_KINDS and (looks_like_markdown(content) or "\n" in content)


def rich_renderable(kind: str, content: str) -> Text | Group:
    if kind == "banner":
        return Text(content, style="bold")
    if kind == "meta":
        return Text(content, style="dim")
    if uses_markdown(kind, content):
        return Group(Padding(Markdown(content), 0))
    return Text(content)


@dataclass(slots=True)
class ChatRuntime:
    config: Config
    name: str | None = None
    client: AsyncOpenAI | None = None
    session: Session | None = None
    own_client: bool = False
    pending_prompts: list[QueuedPrompt] = field(default_factory=list)
    processing_task: asyncio.Task[None] | None = None
    current_reply_task: asyncio.Task[dict[str, Any]] | None = None
    entries: list[Entry] = field(default_factory=list)
    live_entry: Entry | None = None
    notify: Callable[[], None] = field(default=lambda: None, repr=False)

    def require_session(self) -> Session:
        if self.session is None:
            raise RuntimeError("chat session is not ready")
        return self.session

    def require_client(self) -> AsyncOpenAI:
        if self.client is None:
            raise RuntimeError("chat session is not ready")
        return self.client

    def set_notifier(self, notify: Callable[[], None]) -> None:
        self.notify = notify

    def save_queue(self) -> None:
        if self.session is None:
            return
        self.session = replace_queued_prompts(self.require_session(), self.pending_prompts)

    def display_entries(self) -> list[Entry]:
        return [*self.entries, *([self.live_entry] if self.live_entry else [])]

    def append_entry(self, kind: str, content: str, *, notify: bool = True) -> None:
        self.entries.append(Entry(kind, content))
        if notify:
            self.notify()

    def cli_session(self, workspace: Path, name: str | None = None) -> Session:
        if name is None and (existing := existing_cli_session(self.config.sessions_dir, workspace)):
            return existing
        return cli_session(self.config.sessions_dir, session_name(name), workspace=workspace)

    def restore_queue(self) -> None:
        if self.session is None or not self.session.queued_prompts:
            return
        self.pending_prompts = [QueuedPrompt(prompt.content, True) for prompt in self.session.queued_prompts]
        self.save_queue()

    def start_client(self) -> None:
        if self.client is None:
            self.client = AsyncOpenAI(api_key=self.config.openai_api_key)
            self.own_client = True

    async def start(self) -> None:
        if not self.config.openai_api_key:
            raise RuntimeError(f"openai.api_key is missing. Add it to {self.config.config_file}")
        self.session = self.cli_session(Path.cwd(), self.name)
        self.restore_queue()
        self.start_client()
        session = self.require_session()
        self.entries = [
            Entry("banner", " faltoochat "),
            Entry("meta", f"session: {session.name} ({session.id})"),
            Entry("meta", f"workspace: {session.workspace}"),
            Entry("meta", help_text()),
            *history_entries(session),
        ]

    async def close(self) -> None:
        if self.processing_task is not None:
            await self.processing_task
            self.processing_task = None
        if self.current_reply_task is not None:
            try:
                await self.current_reply_task
            except asyncio.CancelledError:
                pass
        if self.client and self.own_client:
            await self.client.close()

    async def submit(self, prompt: str) -> bool:
        text = prompt.strip()
        if not text:
            return True
        if (command_result := await self.handle_command(text)) is not None:
            return command_result
        self.enqueue_prompt(text)
        self.notify()
        self.ensure_processing()
        return True

    def queued_prompts(self) -> tuple[str, ...]:
        return tuple(prompt.content for prompt in self.pending_prompts)

    def queued_prompt_items(self) -> tuple[QueuedPrompt, ...]:
        return tuple(self.pending_prompts)

    def enqueue_prompt(self, prompt: str) -> None:
        self.pending_prompts.append(QueuedPrompt(prompt))
        self.save_queue()

    def pop_next_prompt(self) -> str | None:
        for index, prompt in enumerate(self.pending_prompts):
            if not prompt.paused:
                value = self.pending_prompts.pop(index).content
                self.save_queue()
                return value
        return None

    def remove_prompt(self, index: int) -> str | None:
        if 0 <= index < len(self.pending_prompts):
            prompt = self.pending_prompts.pop(index)
            self.save_queue()
            self.notify()
            return prompt.content
        return None

    def replace_prompt(self, index: int, prompt: str) -> bool:
        if 0 <= index < len(self.pending_prompts):
            self.pending_prompts[index].content = prompt
            self.save_queue()
            self.notify()
            return True
        return False

    def toggle_prompt_paused(self, index: int) -> bool | None:
        if 0 <= index < len(self.pending_prompts):
            prompt = self.pending_prompts[index]
            prompt.paused = not prompt.paused
            self.save_queue()
            self.notify()
            return prompt.paused
        return None

    def move_prompt(self, index: int, target: int) -> int | None:
        if not (0 <= index < len(self.pending_prompts) and 0 <= target < len(self.pending_prompts)):
            return None
        prompt = self.pending_prompts.pop(index)
        self.pending_prompts.insert(target, prompt)
        self.save_queue()
        self.notify()
        return target

    async def handle_command(self, text: str) -> bool | None:
        match text:
            case "/help":
                self.append_entry("meta", help_text())
                return True
            case "/tree":
                session = self.require_session()
                open_in_default_editor(session.messages_file)
                self.append_entry("opened", str(session.messages_file))
                return True
            case "/reset":
                session = self.require_session()
                self.session = self.cli_session(session.workspace, default_session_name())
                self.pending_prompts = []
                new_session = self.require_session()
                self.append_entry("meta", f"new session: {new_session.name} ({new_session.id})")
                return True
            case "/exit":
                return False
            case _:
                return None

    def ensure_processing(self) -> None:
        if self.processing_task is None or self.processing_task.done():
            self.processing_task = asyncio.create_task(self.process_pending())

    async def process_pending(self) -> None:
        while prompt := self.pop_next_prompt():
            self.notify()
            await self.handle_prompt(prompt)
        self.notify()

    async def wait_until_idle(self) -> None:
        if self.processing_task is not None:
            await self.processing_task
            self.processing_task = None

    def interrupt(self) -> bool:
        if self.current_reply_task is None or self.current_reply_task.done():
            return False
        self.current_reply_task.cancel()
        return True

    def close_stream(self, state: StreamState) -> None:
        if state.active_kind is None or self.live_entry is None:
            return
        self.entries.append(self.live_entry)
        self.live_entry = None
        state.active_kind = None
        self.notify()

    def replace_last_bot_entry(self, content: str) -> None:
        for index in range(len(self.entries) - 1, -1, -1):
            if self.entries[index].kind == "bot":
                self.entries[index] = Entry("bot", content)
                return
        self.entries.append(Entry("bot", content))

    def stream_delta(self, state: StreamState, kind: str, delta: str) -> None:
        if not delta:
            return
        if state.active_kind != kind:
            self.close_stream(state)
            state.active_kind = kind
            self.live_entry = Entry(kind, "")
        if kind == "bot":
            state.saw_bot = True
        if kind == "thinking":
            state.saw_thinking = True
        if self.live_entry is not None and delta:
            self.live_entry = Entry(kind, self.live_entry.content + delta)
            self.notify()

    def store_assistant_turn(self, result: dict[str, Any]) -> Turn:
        answer = result["text"]
        turn = Turn(role="assistant", content=answer, created_at="", items=tuple(result["output_items"]))
        self.session = add_turn(
            self.require_session(),
            "assistant",
            answer,
            items=result["output_items"],
            usage=result["usage"],
            instructions=result["instructions"],
        )
        return turn

    def render_assistant_turn(self, turn: Turn, state: StreamState) -> None:
        self.entries.extend(
            entry
            for item in turn.items
            if item_key(item) not in state.tool_keys
            for entry in item_entries(item)
            if not (state.saw_thinking and entry.kind == "thinking")
        )
        if state.saw_bot:
            self.replace_last_bot_entry(turn.content)
        else:
            self.entries.append(Entry("bot", turn.content))
        self.notify()

    async def handle_prompt(self, prompt: str) -> None:
        session = add_turn(self.require_session(), "user", prompt)
        self.session = session
        self.append_entry("you", prompt)
        state = StreamState()

        async def on_text_delta(delta: str) -> None:
            self.stream_delta(state, "bot", delta)

        async def on_reasoning_delta(delta: str) -> None:
            self.stream_delta(state, "thinking", delta)

        async def on_reasoning_done() -> None:
            if state.active_kind == "thinking":
                self.close_stream(state)

        async def on_output_item(item: dict[str, Any]) -> None:
            if text := tool_entry(item):
                self.close_stream(state)
                self.append_entry("tool", text)
                if key := item_key(item):
                    state.tool_keys.add(key)

        self.current_reply_task = asyncio.create_task(
            stream_reply(
                self.require_client(),
                self.config,
                session,
                session_items(session),
                on_text_delta=on_text_delta,
                on_reasoning_delta=on_reasoning_delta,
                on_reasoning_done=on_reasoning_done,
                on_output_item=on_output_item,
            )
        )
        try:
            result = await self.current_reply_task
        except asyncio.CancelledError:
            self.close_stream(state)
            self.append_entry("meta", "reply interrupted")
            return
        except Exception as exc:
            self.close_stream(state)
            self.append_entry("error", str(exc))
            return
        finally:
            self.current_reply_task = None

        self.close_stream(state)
        self.render_assistant_turn(self.store_assistant_turn(result), state)


class Composer(TextArea):
    class Submitted(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    def on_key(self, event: Any) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.post_message(self.Submitted(self.text))
            return
        if event.key in {"shift+enter", "ctrl+j"}:
            event.prevent_default()
            event.stop()
            self.insert("\n")


class QueueItem(Horizontal):
    class Picked(Message):
        def __init__(self, index: int) -> None:
            self.index = index
            super().__init__()

    class DragStart(Message):
        def __init__(self, index: int) -> None:
            self.index = index
            super().__init__()

    class DragFinish(Message):
        def __init__(self, index: int) -> None:
            self.index = index
            super().__init__()

    def __init__(self, index: int, prompt: QueuedPrompt, *, selected: bool = False) -> None:
        self.index = index
        self.content = prompt.content
        self.paused = prompt.paused
        self.selected = selected
        super().__init__(classes="queue-item")

    def compose(self) -> ComposeResult:
        yield Static(Text(self.content, overflow="ellipsis", no_wrap=True), classes="queue-text")
        yield Button("▶" if self.paused else "⏸", classes="queue-pause")
        yield Button("×", classes="queue-delete")

    def on_mouse_down(self, event: Any) -> None:
        event.stop()
        self.post_message(self.DragStart(self.index))

    def on_mouse_up(self, event: Any) -> None:
        event.stop()
        self.post_message(self.DragFinish(self.index))

    def on_click(self, event: Any) -> None:
        event.stop()
        self.post_message(self.Picked(self.index))


class EntryBlock(Vertical):
    DEFAULT_CSS = """
    EntryBlock {
        width: 1fr;
        max-width: 80;
        height: auto;
        margin: 0 0 1 0;
    }

    EntryBlock > .body {
        width: 1fr;
        height: auto;
        padding: 0 1;
        background: transparent;
        color: $text;
    }

    EntryBlock.entry-you > .body {
        background: $primary 8%;
    }

    EntryBlock.entry-bot > .body {
        background: $surface;
    }

    EntryBlock.entry-thinking > .body {
        color: $text-muted;
        background: $surface-active;
    }

    EntryBlock.entry-tool > .body {
        color: $secondary;
        background: $secondary 8%;
    }

    EntryBlock.entry-error > .body {
        color: $error;
        background: $error 8%;
    }

    EntryBlock.entry-opened > .body {
        color: $accent;
        background: $accent 8%;
    }

    EntryBlock.entry-banner > .body,
    EntryBlock.entry-meta > .body {
        background: transparent;
    }

    EntryBlock.entry-banner > .body {
        color: $warning;
        text-style: bold;
    }

    EntryBlock.entry-meta > .body {
        color: $text-disabled;
    }
    """

    def __init__(self, entry: Entry) -> None:
        self.entry = entry
        super().__init__(classes=entry_class(entry.kind))

    def compose(self) -> ComposeResult:
        kind = self.entry.kind
        content = self.entry.content
        if kind in {"banner", "meta"} or not self.uses_markdown():
            yield Static(content, id="body", classes="body")
            return
        yield TextualMarkdown(content, id="body", classes="body")

    def uses_markdown(self) -> bool:
        return uses_markdown(self.entry.kind, self.entry.content)

    def same_layout(self, entry: Entry) -> bool:
        return (
            self.entry.kind == entry.kind
            and self.uses_markdown() == uses_markdown(entry.kind, entry.content)
            and ("\n" in self.entry.content) == ("\n" in entry.content)
        )

    def set_entry(self, entry: Entry) -> bool:
        if not self.same_layout(entry):
            return False
        self.entry = entry
        if self.uses_markdown():
            self.query_one("#body", TextualMarkdown).update(entry.content)
            return True
        self.query_one("#body", Static).update(entry.content)
        return True


class LiveMarkdownBlock(Vertical):
    DEFAULT_CSS = EntryBlock.DEFAULT_CSS

    def __init__(self, entry: Entry) -> None:
        self.entry = entry
        self._stream: Any = None
        self._pending: asyncio.Task[None] | None = None
        super().__init__(classes=entry_class(entry.kind))

    def compose(self) -> ComposeResult:
        yield TextualMarkdown("", id="body", classes="body")

    async def on_mount(self) -> None:
        body = self.query_one("#body", TextualMarkdown)
        self._stream = TextualMarkdown.get_stream(body)
        if self.entry.content:
            self._write(self.entry.content)

    async def on_unmount(self) -> None:
        if self._pending is not None:
            await self._pending
        if self._stream is not None:
            await self._stream.stop()

    def _write(self, chunk: str) -> None:
        if self._stream is None or not chunk:
            return

        async def run(after: asyncio.Task[None] | None) -> None:
            if after is not None:
                await after
            await self._stream.write(chunk)

        self._pending = asyncio.create_task(run(self._pending))

    def set_entry(self, entry: Entry) -> bool:
        if entry.kind != self.entry.kind or entry.kind not in {"bot", "thinking"}:
            return False
        if entry.content == self.entry.content:
            self.entry = entry
            return True
        if not entry.content.startswith(self.entry.content):
            return False
        delta = entry.content[len(self.entry.content) :]
        self.entry = entry
        if delta:
            self._write(delta)
        return True


class FaltooChatApp(App[None]):
    CSS = """
    App {
        color: $text;
        background: $background;
        link-background: transparent;
        link-background-hover: transparent;
        link-color: $primary;
        link-color-hover: $accent;
        link-style: bold underline;
        link-style-hover: bold underline;
    }

    Screen {
        layout: vertical;
        color: $text;
        background: $background;
    }

    #shell {
        height: 1fr;
        background: $background;
    }

    #transcript {
        width: 1fr;
        height: 1fr;
        layout: vertical;
        align-horizontal: center;
        overflow-y: auto;
        background: $background;
        padding: 1 2;
        border: none;
    }

    #queue {
        height: auto;
        max-height: 8;
        layout: vertical;
        background: $background;
        border: none;
        padding: 0 1;
    }

    .queue-item {
        height: auto;
        min-height: 1;
        align: left middle;
        padding: 0 1;
        background: $background;
        color: $text;
        margin: 0 0 1 0;
    }

    .queue-item.-selected {
        text-style: bold underline;
    }

    .queue-text {
        width: 1fr;
        height: auto;
        color: $text;
    }

    .queue-delete {
        min-width: 3;
        width: 3;
        height: 1;
        padding: 0;
        margin: 0 0 0 1;
        background: transparent;
        color: $error;
        border: none;
    }

    .queue-pause {
        min-width: 3;
        width: 3;
        height: 1;
        padding: 0;
        margin: 0 0 0 1;
        background: transparent;
        color: $text-muted;
        border: none;
    }

    #footer {
        width: 80;
        height: auto;
        layout: vertical;
    }

    #composer {
        width: 1fr;
        height: 6;
        min-height: 3;
        background: $surface;
        color: $text;
        padding: 0 1;
        border: none;
    }

    #status {
        width: 1fr;
        height: 1;
        padding: 0 2;
        background: $surface;
        color: $text-muted;
        text-style: bold;
    }

    Markdown {
        background: transparent;
        link-background: transparent;
        link-background-hover: transparent;
        link-color: $primary;
        link-color-hover: $accent;
        link-style: bold underline;
        link-style-hover: bold underline;
    }

    MarkdownBlock {
        link-background: transparent;
        link-background-hover: transparent;
        link-color: $primary;
        link-color-hover: $accent;
        link-style: bold underline;
        link-style-hover: bold underline;
    }

    Markdown MarkdownFence {
        background: transparent;
        color: $text;
    }

    Markdown MarkdownFence > Label {
        background: transparent;
        color: $text;
    }

    Markdown MarkdownBlock > .code_inline {
        background: $surface;
        color: $warning;
    }

    #transcript {
        scrollbar-background: $background;
        scrollbar-background-hover: $background;
        scrollbar-background-active: $background;
        scrollbar-color: $text-muted;
        scrollbar-color-hover: $primary;
        scrollbar-color-active: $accent;
        scrollbar-corner-color: $background;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "interrupt_or_quit", "Interrupt", show=False),
        Binding("ctrl+e", "edit_selected_queue", "Edit Queue", show=False),
        Binding("ctrl+p", "toggle_selected_queue_pause", "Pause Queue", show=False),
        Binding("delete", "delete_selected_queue", "Delete Queue", show=False),
        Binding("backspace", "delete_selected_queue", "Delete Queue", show=False),
        Binding("alt+up", "move_selected_queue_up", "Queue Up", show=False),
        Binding("alt+down", "move_selected_queue_down", "Queue Down", show=False),
    ]

    def __init__(
        self,
        config: Config | None = None,
        name: str | None = None,
        client: AsyncOpenAI | None = None,
        terminal_dark: bool | None = None,
    ) -> None:
        super().__init__()
        if terminal_dark is not None:
            self.theme = "textual-dark" if terminal_dark else "textual-light"
        self.runtime = build_chat_runtime(config=config, name=name, client=client)
        self._snapshot: tuple[tuple[str, str], ...] = ()
        self._blocks: list[EntryBlock] = []
        self._live_block: EntryBlock | LiveMarkdownBlock | None = None
        self._queue_snapshot: tuple[QueuedPrompt, ...] = ()
        self._queue_selected: int | None = None
        self._queue_drag_index: int | None = None

    def make_entry_block(self, entry: Entry) -> EntryBlock | LiveMarkdownBlock:
        return LiveMarkdownBlock(entry) if entry.kind in MARKDOWN_KINDS else EntryBlock(entry)

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            yield VerticalScroll(id="transcript")
            with Center():
                with Vertical(id="footer"):
                    yield Vertical(id="queue")
                    yield Composer(
                        id="composer",
                        text="",
                        soft_wrap=True,
                        show_line_numbers=False,
                        highlight_cursor_line=False,
                        placeholder="Type a message or /help",
                    )
                    yield Static("", id="status")

    def transcript(self) -> VerticalScroll:
        return self.query_one("#transcript", VerticalScroll)

    def composer(self) -> Composer:
        return self.query_one("#composer", Composer)

    def queue(self) -> Vertical:
        return self.query_one("#queue", Vertical)

    def status(self) -> Static:
        return self.query_one("#status", Static)

    async def on_mount(self) -> None:
        self.runtime.set_notifier(self.sync_view)
        await self.runtime.start()
        self.sync_view(force=True)
        self.set_interval(0.05, self.refresh_ui)
        self.call_after_refresh(self.composer().focus)

    async def on_unmount(self) -> None:
        await self.runtime.close()

    async def on_composer_submitted(self, message: Composer.Submitted) -> None:
        composer = self.composer()
        composer.load_text("")
        if not await self.runtime.submit(message.value):
            self.runtime.pending_prompts.clear()
            self.runtime.interrupt()
            self.exit()
            return
        self.sync_view(force=True)

    def refresh_ui(self) -> None:
        try:
            self.sync_view()
        except NoMatches:
            return

    def sync_view(self, force: bool = False) -> None:
        self.refresh_queue(force=force)
        self.refresh_status()
        self.refresh_transcript(force=force)

    def refresh_status(self) -> None:
        self.status().update(
            input_hint(
                self.runtime.config,
                replying=self.runtime.current_reply_task is not None,
                queued=len(self.runtime.pending_prompts),
                queue_selected=self._queue_selected is not None,
            )
        )

    def normalize_queue_selection(self) -> None:
        queued = self.runtime.queued_prompts()
        if not queued:
            self._queue_selected = None
        elif self._queue_selected is None or self._queue_selected >= len(queued):
            self._queue_selected = len(queued) - 1

    def refresh_queue(self, *, force: bool = False) -> None:
        queued = self.runtime.queued_prompt_items()
        if not force and queued == self._queue_snapshot:
            return
        self.normalize_queue_selection()
        queue = self.queue()
        queue.remove_children()
        items = [
            QueueItem(index, prompt, selected=index == self._queue_selected)
            for index, prompt in enumerate(queued)
        ]
        for item in items:
            item.set_class(item.index == self._queue_selected, "-selected")
        if items:
            queue.mount(*items)
            queue.display = True
        else:
            queue.display = False
        self._queue_snapshot = queued

    def refresh_transcript(self, *, force: bool = False) -> None:
        entries = list(self.runtime.entries)
        live = self.runtime.live_entry
        snapshot = tuple((entry.kind, entry.content) for entry in [*entries, *([live] if live else [])])
        if not force and snapshot == self._snapshot:
            return

        transcript = self.transcript()
        at_end = transcript.is_vertical_scroll_end
        previous_scroll = transcript.scroll_y
        rendered = tuple((entry.kind, entry.content) for entry in entries)
        append_only = rendered[: len(self._blocks)] == tuple((block.entry.kind, block.entry.content) for block in self._blocks)

        if force or not append_only:
            transcript.remove_children()
            self._blocks = []
            self._live_block = None
            if entries:
                self._blocks = [EntryBlock(entry) for entry in entries]
                transcript.mount(*self._blocks)
        else:
            new_entries = entries[len(self._blocks) :]
            if new_entries:
                blocks = [EntryBlock(entry) for entry in new_entries]
                self._blocks.extend(blocks)
                transcript.mount(*blocks)

        if live is None:
            if self._live_block is not None:
                self._live_block.remove()
                self._live_block = None
        elif self._live_block is None:
            self._live_block = self.make_entry_block(live)
            transcript.mount(self._live_block)
        elif not self._live_block.set_entry(live):
            self._live_block.remove()
            self._live_block = self.make_entry_block(live)
            transcript.mount(self._live_block)

        should_scroll_end = force or at_end or self.runtime.current_reply_task is not None
        if should_scroll_end:
            self.call_after_refresh(lambda: transcript.scroll_end(animate=False, immediate=True))
        else:
            self.call_after_refresh(
                lambda: transcript.scroll_to(y=previous_scroll, animate=False, immediate=True)
            )
        self._snapshot = snapshot

    def action_interrupt_or_quit(self) -> None:
        if not self.runtime.interrupt():
            self.exit()

    def edit_queue(self, index: int) -> None:
        if (prompt := self.runtime.remove_prompt(index)) is None:
            return
        self._queue_selected = min(index, len(self.runtime.pending_prompts) - 1) if self.runtime.pending_prompts else None
        composer = self.composer()
        composer.load_text(prompt)
        composer.focus()
        self.sync_view(force=True)

    def delete_queue(self, index: int) -> None:
        if self.runtime.remove_prompt(index) is None:
            return
        self._queue_selected = min(index, len(self.runtime.pending_prompts) - 1) if self.runtime.pending_prompts else None
        self.sync_view(force=True)

    def move_queue(self, index: int, target: int) -> None:
        if (new_index := self.runtime.move_prompt(index, target)) is None:
            return
        self._queue_selected = new_index
        self.sync_view(force=True)

    @on(QueueItem.Picked)
    def on_queue_item_picked(self, message: QueueItem.Picked) -> None:
        if self._queue_drag_index is not None and self._queue_drag_index != message.index:
            return
        self._queue_selected = message.index
        self.edit_queue(message.index)

    @on(QueueItem.DragStart)
    def on_queue_item_drag_start(self, message: QueueItem.DragStart) -> None:
        self._queue_drag_index = message.index
        self._queue_selected = message.index
        self.sync_view(force=True)

    @on(QueueItem.DragFinish)
    def on_queue_item_drag_finish(self, message: QueueItem.DragFinish) -> None:
        if self._queue_drag_index is None:
            return
        source = self._queue_drag_index
        self._queue_drag_index = None
        if source == message.index:
            return
        self.move_queue(source, message.index)

    @on(Button.Pressed, ".queue-delete")
    def on_queue_delete_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        parent = event.button.parent
        if not isinstance(parent, QueueItem):
            return
        self.delete_queue(parent.index)

    @on(Button.Pressed, ".queue-pause")
    def on_queue_pause_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        parent = event.button.parent
        if not isinstance(parent, QueueItem):
            return
        self.toggle_queue_pause(parent.index)

    def action_edit_selected_queue(self) -> None:
        if self._queue_selected is not None:
            self.edit_queue(self._queue_selected)

    def toggle_queue_pause(self, index: int) -> None:
        if (paused := self.runtime.toggle_prompt_paused(index)) is not None:
            self._queue_selected = index
            if not paused:
                self.runtime.ensure_processing()
            self.sync_view(force=True)

    def action_delete_selected_queue(self) -> None:
        if self._queue_selected is not None:
            self.delete_queue(self._queue_selected)

    def action_toggle_selected_queue_pause(self) -> None:
        if self._queue_selected is not None:
            self.toggle_queue_pause(self._queue_selected)

    def action_move_selected_queue_up(self) -> None:
        if self._queue_selected not in {None, 0}:
            self.move_queue(self._queue_selected, self._queue_selected - 1)

    def action_move_selected_queue_down(self) -> None:
        if self._queue_selected is None:
            return
        if self._queue_selected < len(self.runtime.pending_prompts) - 1:
            self.move_queue(self._queue_selected, self._queue_selected + 1)


def build_chat_runtime(
    config: Config | None = None,
    name: str | None = None,
    client: AsyncOpenAI | None = None,
) -> ChatRuntime:
    return ChatRuntime(config=config or build_config(), name=name, client=client)


def build_chat_app(
    config: Config | None = None,
    name: str | None = None,
    client: AsyncOpenAI | None = None,
    terminal_dark: bool | None = None,
) -> FaltooChatApp:
    return FaltooChatApp(config=config, name=name, client=client, terminal_dark=terminal_dark)


async def run_chat(config: Config | None = None, name: str | None = None) -> None:
    await build_chat_app(config=config, name=name, terminal_dark=terminal_background_dark()).run_async()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="faltoochat")
    parser.add_argument("--name", help="optional session name")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        build_chat_app(name=args.name, terminal_dark=terminal_background_dark()).run()
    except KeyboardInterrupt:
        return 130
    return 0
