import argparse
import asyncio
import json
import subprocess
import sys
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Markdown as TextualMarkdown
from textual.widgets import Static, TextArea

from faltoobot.agent import stream_reply
from faltoobot.config import Config, build_config
from faltoobot.store import (
    Session,
    Turn,
    add_turn,
    cli_session,
    existing_cli_session,
    session_items,
)

RICH_KINDS = frozenset({"you", "bot", "thinking", "tool"})
PREFIX_STYLES = {
    "you": "bold #ffb347",
    "bot": "bold #76c7ff",
    "thinking": "bold #93a8bd",
    "tool": "bold #7fd4b6",
    "error": "bold #ff7b72",
    "opened": "bold #8ea4bc",
}
BODY_STYLES = {
    "you": "#fff4df",
    "bot": "#e8f0f8",
    "thinking": "#aab9c9",
    "tool": "#cdeee3",
    "error": "#ffd5cf",
    "opened": "#d7e3ef",
}
STATUS_STYLE = "bold #8ea4bc on #0b1520"
TURN_KIND = {"user": "you", "assistant": "bot"}
MAX_TOOL_LINES = 8


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


def input_hint(config: Config, *, replying: bool = False, queued: int = 0) -> str:
    parts = [status_text(config), "Enter send", "Shift+Enter newline", "Ctrl+C interrupt"]
    if replying:
        parts.append("replying")
    if queued:
        parts.append(f"queued {queued}")
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


def render_line(kind: str, content: str) -> Text:
    if kind == "meta":
        return Text(content, style="dim #8ea4bc")
    if kind == "banner":
        return Text(content, style="bold #0a0c10 on #ffb347")
    text = Text()
    text.append(f"{kind}> ", style=PREFIX_STYLES.get(kind, "bold"))
    text.append(content, style=BODY_STYLES.get(kind, "#eef3f9"))
    return text


def looks_like_markdown(content: str) -> bool:
    return any(token in content for token in ("**", "__", "`", "[", "](", "\n#", "\n-", "\n1. "))


def render_markdown_block(kind: str, content: str) -> Group:
    return Group(render_line(kind, ""), Padding(Markdown(content), (0, 0, 0, 2)))


def rich_renderable(kind: str, content: str) -> Text | Group:
    if kind in RICH_KINDS and looks_like_markdown(content):
        return render_markdown_block(kind, content)
    return render_line(kind, content)


def stream_text(kind: str, delta: str) -> str:
    if kind != "thinking":
        return delta
    return delta.replace("**", "").replace("`", "")


@dataclass(slots=True)
class ChatRuntime:
    config: Config
    name: str | None = None
    client: AsyncOpenAI | None = None
    session: Session | None = None
    own_client: bool = False
    pending_prompts: deque[str] = field(default_factory=deque)
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

    def display_entries(self) -> list[Entry]:
        return [*self.entries, self.live_entry] if self.live_entry else list(self.entries)

    def append_entry(self, kind: str, content: str, *, notify: bool = True) -> None:
        self.entries.append(Entry(kind, content))
        if notify:
            self.notify()

    def cli_session(self, workspace: Path, name: str | None = None) -> Session:
        if name is None and (existing := existing_cli_session(self.config.sessions_dir, workspace)):
            return existing
        return cli_session(self.config.sessions_dir, session_name(name), workspace=workspace)

    def start_client(self) -> None:
        if self.client is None:
            self.client = AsyncOpenAI(api_key=self.config.openai_api_key)
            self.own_client = True

    async def start(self) -> None:
        if not self.config.openai_api_key:
            raise RuntimeError(f"openai.api_key is missing. Add it to {self.config.config_file}")
        self.session = self.cli_session(Path.cwd(), self.name)
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
        await self.wait_until_idle()
        if self.client and self.own_client:
            await self.client.close()

    async def submit(self, prompt: str) -> bool:
        text = prompt.strip()
        if not text:
            return True
        if (command_result := await self.handle_command(text)) is not None:
            return command_result
        self.pending_prompts.append(text)
        self.notify()
        self.ensure_processing()
        return True

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
        while self.pending_prompts:
            await self.handle_prompt(self.pending_prompts.popleft())
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
        text = stream_text(kind, delta)
        if self.live_entry is not None and text:
            self.live_entry = Entry(kind, self.live_entry.content + text)
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
        if not state.saw_bot:
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


class EntryBlock(Vertical):
    DEFAULT_CSS = """
    EntryBlock {
        height: auto;
    }

    EntryBlock > .body {
        height: auto;
        padding-left: 2;
    }

    EntryBlock > .inline {
        height: auto;
    }

    EntryBlock > .inline > Static {
        height: auto;
    }
    """

    def __init__(self, entry: Entry) -> None:
        self.entry = entry
        super().__init__()

    def compose(self) -> ComposeResult:
        kind = self.entry.kind
        content = self.entry.content
        if kind in {"banner", "meta"}:
            yield Static(render_line(kind, content), classes="body")
            return
        if self.uses_markdown():
            yield Static(render_line(kind, ""), id="prefix")
            yield TextualMarkdown(content, id="body", classes="body")
            return
        if "\n" in content:
            yield Static(render_line(kind, ""), id="prefix")
            yield Static(Text(content, style=BODY_STYLES.get(kind, "#eef3f9")), id="body", classes="body")
            return
        with Horizontal(classes="inline"):
            yield Static(Text(f"{kind}> ", style=PREFIX_STYLES.get(kind, "bold")), id="prefix")
            yield Static(Text(content, style=BODY_STYLES.get(kind, "#eef3f9")), id="body")

    def uses_markdown(self) -> bool:
        return self.entry.kind in RICH_KINDS and (
            looks_like_markdown(self.entry.content) or "\n" in self.entry.content
        )

    def same_layout(self, entry: Entry) -> bool:
        return self.entry.kind == entry.kind and self.uses_markdown() == (
            entry.kind in RICH_KINDS and (looks_like_markdown(entry.content) or "\n" in entry.content)
        ) and ("\n" in self.entry.content) == ("\n" in entry.content)

    def set_entry(self, entry: Entry) -> bool:
        if not self.same_layout(entry):
            return False
        self.entry = entry
        if entry.kind in {"banner", "meta"}:
            self.query_one(".body", Static).update(render_line(entry.kind, entry.content))
            return True
        if self.uses_markdown():
            self.query_one("#body", TextualMarkdown).update(entry.content)
            return True
        self.query_one("#body", Static).update(Text(entry.content, style=BODY_STYLES.get(entry.kind, "#eef3f9")))
        return True


class FaltooChatApp(App[None]):
    CSS = """
    Screen {
        layout: vertical;
        background: #171411;
        color: #f8e9c7;
    }

    #shell {
        height: 1fr;
    }

    #transcript {
        height: 1fr;
        layout: vertical;
        overflow-y: auto;
        background: #14100d;
        padding: 1 2;
        border: none;
    }

    #composer {
        height: 6;
        min-height: 3;
        background: #1f1713;
        color: #fff4df;
        padding: 0 1;
        border: none;
    }

    #status {
        height: 1;
        padding: 0 2;
        background: #0b1520;
        color: #8ea4bc;
        text-style: bold;
    }
    """

    BINDINGS = [Binding("ctrl+c", "interrupt_or_quit", "Interrupt", show=False)]

    def __init__(
        self,
        config: Config | None = None,
        name: str | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        super().__init__()
        self.runtime = build_chat_runtime(config=config, name=name, client=client)
        self._snapshot: tuple[tuple[str, str], ...] = ()
        self._blocks: list[EntryBlock] = []
        self._live_block: EntryBlock | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            yield VerticalScroll(id="transcript")
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
        self.sync_view()

    def sync_view(self, force: bool = False) -> None:
        self.refresh_status()
        self.refresh_transcript(force=force)

    def refresh_status(self) -> None:
        self.status().update(
            Text(
                input_hint(
                    self.runtime.config,
                    replying=self.runtime.current_reply_task is not None,
                    queued=len(self.runtime.pending_prompts),
                ),
                style=STATUS_STYLE,
            )
        )

    def refresh_transcript(self, *, force: bool = False) -> None:
        entries = list(self.runtime.entries)
        live = self.runtime.live_entry
        snapshot = tuple((entry.kind, entry.content) for entry in [*entries, *( [live] if live else [] )])
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
            self._live_block = EntryBlock(live)
            transcript.mount(self._live_block)
        elif not self._live_block.set_entry(live):
            self._live_block.remove()
            self._live_block = EntryBlock(live)
            transcript.mount(self._live_block)

        if at_end:
            transcript.scroll_end(animate=False, immediate=True)
        else:
            transcript.scroll_to(y=previous_scroll, animate=False, immediate=True)
        self._snapshot = snapshot

    def action_interrupt_or_quit(self) -> None:
        if not self.runtime.interrupt():
            self.exit()


def build_chat_runtime(
    config: Config | None = None,
    name: str | None = None,
    console: Console | None = None,
    client: AsyncOpenAI | None = None,
) -> ChatRuntime:
    _ = console
    return ChatRuntime(config=config or build_config(), name=name, client=client)


def build_chat_app(
    config: Config | None = None,
    name: str | None = None,
    client: AsyncOpenAI | None = None,
) -> FaltooChatApp:
    return FaltooChatApp(config=config, name=name, client=client)


async def run_chat(config: Config | None = None, name: str | None = None) -> None:
    await build_chat_app(config=config, name=name).run_async()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="faltoochat")
    parser.add_argument("--name", help="optional session name")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        build_chat_app(name=args.name).run()
    except KeyboardInterrupt:
        return 130
    return 0
