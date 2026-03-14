import argparse
import asyncio
import json
import subprocess
import sys
import textwrap
from collections import deque
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from openai import AsyncOpenAI
from ratatui_py import DrawCmd, Paragraph, Style, rgb, terminal_session
from ratatui_py.types import KeyCode, KeyEvt, KeyMods, MouseEvt, MouseKind, Rect

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

MAX_TOOL_LINES = 8
MAX_INPUT_LINES = 6
TICK_MS = 50

TRANSCRIPT_BG = Style(bg=rgb(21, 20, 18))
STATUS_STYLE = Style(fg=rgb(158, 198, 207), bg=rgb(11, 21, 32))
INPUT_STYLE = Style(fg=rgb(255, 244, 223), bg=rgb(30, 24, 20))
PREFIX_STYLES = {
    "you": Style(fg=rgb(255, 179, 71)).bold(),
    "bot": Style(fg=rgb(118, 199, 255)).bold(),
    "thinking": Style(fg=rgb(147, 168, 189)).bold(),
    "tool": Style(fg=rgb(127, 212, 182)).bold(),
    "error": Style(fg=rgb(255, 123, 114)).bold(),
    "opened": Style(fg=rgb(142, 164, 188)).bold(),
    "banner": Style(fg=rgb(10, 12, 16), bg=rgb(255, 179, 71)).bold(),
}
BODY_STYLES = {
    "you": Style(fg=rgb(255, 244, 223)),
    "bot": Style(fg=rgb(248, 233, 199)),
    "thinking": Style(fg=rgb(181, 176, 223)),
    "tool": Style(fg=rgb(205, 238, 227)),
    "error": Style(fg=rgb(255, 213, 207)),
    "opened": Style(fg=rgb(215, 227, 239)),
    "meta": Style(fg=rgb(142, 164, 188)),
    "banner": PREFIX_STYLES["banner"],
}
TURN_KIND = {"user": "you", "assistant": "bot"}


class TerminalLike(Protocol):
    def size(self) -> tuple[int, int]: ...

    def draw_frame(self, cmds: list[DrawCmd]) -> bool: ...

    def next_event_typed(self, timeout_ms: int) -> KeyEvt | MouseEvt | Any | None: ...

    def set_cursor_position(self, x: int, y: int) -> None: ...

    def show_cursor(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Entry:
    kind: str
    content: str


@dataclass(slots=True)
class StreamState:
    saw_bot: bool = False
    saw_thinking: bool = False
    tool_keys: set[str] = field(default_factory=set)


@dataclass(slots=True)
class InputBuffer:
    text: str = ""
    cursor: int = 0

    def insert(self, value: str) -> None:
        self.text = f"{self.text[:self.cursor]}{value}{self.text[self.cursor:]}"
        self.cursor += len(value)

    def backspace(self) -> None:
        if self.cursor == 0:
            return
        self.text = f"{self.text[:self.cursor - 1]}{self.text[self.cursor:]}"
        self.cursor -= 1

    def delete(self) -> None:
        if self.cursor >= len(self.text):
            return
        self.text = f"{self.text[:self.cursor]}{self.text[self.cursor + 1:]}"

    def move(self, delta: int) -> None:
        self.cursor = max(0, min(len(self.text), self.cursor + delta))

    def home(self) -> None:
        self.cursor = line_start(self.text, self.cursor)

    def end(self) -> None:
        self.cursor = line_end(self.text, self.cursor)

    def move_vertical(self, delta: int) -> None:
        starts = line_starts(self.text)
        line_index = max(index for index, start in enumerate(starts) if start <= self.cursor)
        column = self.cursor - starts[line_index]
        target = max(0, min(len(starts) - 1, line_index + delta))
        self.cursor = min(starts[target] + column, line_end(self.text, starts[target]))

    def take(self) -> str:
        value = self.text
        self.text = ""
        self.cursor = 0
        return value


@dataclass(slots=True)
class ChatRuntime:
    config: Config
    name: str | None = None
    client: AsyncOpenAI | None = None
    own_client: bool = False
    session: Session | None = None
    entries: list[Entry] = field(default_factory=list)
    pending_prompts: deque[str] = field(default_factory=deque)
    processing_task: asyncio.Task[None] | None = None
    current_reply_task: asyncio.Task[dict[str, Any]] | None = None
    live_kind: str | None = None
    live_text: str = ""

    def require_session(self) -> Session:
        if self.session is None:
            raise RuntimeError("chat session is not ready")
        return self.session

    def require_client(self) -> AsyncOpenAI:
        if self.client is None:
            raise RuntimeError("chat session is not ready")
        return self.client

    def append(self, kind: str, content: str) -> None:
        self.entries.append(Entry(kind, content))

    def display_entries(self) -> list[Entry]:
        if self.live_kind is None:
            return list(self.entries)
        return [*self.entries, Entry(self.live_kind, self.live_text)]

    def start_client(self) -> None:
        if self.client is None:
            self.client = AsyncOpenAI(api_key=self.config.openai_api_key)
            self.own_client = True

    def cli_session(self, workspace: Path, name: str | None = None) -> Session:
        if name is None and (existing := existing_cli_session(self.config.sessions_dir, workspace)):
            return existing
        return cli_session(self.config.sessions_dir, session_name(name), workspace=workspace)

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
        self.ensure_processing()
        return True

    async def handle_command(self, text: str) -> bool | None:
        match text:
            case "/help":
                self.append("meta", help_text())
                return True
            case "/tree":
                session = self.require_session()
                open_in_default_editor(session.messages_file)
                self.append("opened", str(session.messages_file))
                return True
            case "/reset":
                workspace = self.require_session().workspace
                self.session = self.cli_session(workspace, default_session_name())
                session = self.require_session()
                self.append("meta", f"new session: {session.name} ({session.id})")
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

    async def wait_until_idle(self) -> None:
        if self.processing_task is not None:
            await self.processing_task
            self.processing_task = None

    def interrupt(self) -> bool:
        if self.current_reply_task is None or self.current_reply_task.done():
            return False
        self.current_reply_task.cancel()
        return True

    def close_stream(self) -> None:
        if self.live_kind is None:
            return
        self.append(self.live_kind, self.live_text)
        self.live_kind = None
        self.live_text = ""

    def stream_delta(self, state: StreamState, kind: str, delta: str) -> None:
        text = stream_text(kind, delta)
        if not text:
            return
        if self.live_kind != kind:
            self.close_stream()
            self.live_kind = kind
            self.live_text = ""
        if kind == "bot":
            state.saw_bot = True
        if kind == "thinking":
            state.saw_thinking = True
        self.live_text += text

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
            self.append("bot", turn.content)

    async def handle_prompt(self, prompt: str) -> None:
        session = add_turn(self.require_session(), "user", prompt)
        self.session = session
        self.append("you", prompt)
        state = StreamState()

        async def on_text_delta(delta: str) -> None:
            self.stream_delta(state, "bot", delta)

        async def on_reasoning_delta(delta: str) -> None:
            self.stream_delta(state, "thinking", delta)

        async def on_reasoning_done() -> None:
            if self.live_kind == "thinking":
                self.close_stream()

        async def on_output_item(item: dict[str, Any]) -> None:
            if text := tool_entry(item):
                self.close_stream()
                self.append("tool", text)
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
            self.close_stream()
            self.append("meta", "reply interrupted")
            return
        except Exception as exc:
            self.close_stream()
            self.append("error", str(exc))
            return
        finally:
            self.current_reply_task = None

        self.close_stream()
        self.render_assistant_turn(self.store_assistant_turn(result), state)


@dataclass(slots=True)
class ChatUi:
    runtime: ChatRuntime
    input_buffer: InputBuffer = field(default_factory=InputBuffer)
    scroll_from_bottom: int = 0

    def status_line(self) -> str:
        parts = [status_text(self.runtime.config), "Enter send", "Ctrl+J newline", "Ctrl+C interrupt"]
        if self.runtime.current_reply_task is not None:
            parts.append("replying")
        if self.runtime.pending_prompts:
            parts.append(f"queued {len(self.runtime.pending_prompts)}")
        return "  ".join(parts)


def default_session_name() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def help_text() -> str:
    return "Commands: /help, /tree, /reset, /exit"


def session_name(name: str | None) -> str:
    return f"CLI {name or default_session_name()}"


def status_text(config: Config) -> str:
    return f"model: {config.openai_model}  thinking: {config.openai_thinking}"


def input_hint(config: Config) -> str:
    return f"{status_text(config)}  Enter send  Ctrl+J newline  Ctrl+C interrupt"


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
        return ["web search", query] if isinstance(query, str) and query.strip() else ["web search"]
    if item_type.endswith("_call") and not item_type.endswith("_output"):
        detail = item.get("name") or item.get("call_id") or item.get("id")
        label = item_type.replace("_", " ")
        return [label, str(detail)] if detail else [label]
    return []


def tool_entry(item: dict[str, Any]) -> str | None:
    lines = tool_lines(item)
    if not lines:
        return None
    return "\n".join([*lines[: MAX_TOOL_LINES - 1], "..."] if len(lines) > MAX_TOOL_LINES else lines)


def item_entries(item: dict[str, Any]) -> list[Entry]:
    if item.get("type") == "reasoning":
        return [
            Entry("thinking", text)
            for summary in item.get("summary", [])
            if isinstance(summary, dict)
            for text in [summary.get("text")]
            if isinstance(text, str) and text.strip()
        ]
    return [Entry("tool", text)] if (text := tool_entry(item)) else []


def item_key(item: dict[str, Any]) -> str | None:
    value = item.get("id") or item.get("call_id")
    return value if isinstance(value, str) else tool_entry(item)


def turn_entries(turn: Turn) -> list[Entry]:
    return [*(entry for item in turn.items for entry in item_entries(item)), Entry(TURN_KIND.get(turn.role, "bot"), turn.content)]


def history_entries(session: Session) -> list[Entry]:
    return [entry for turn in session.messages for entry in turn_entries(turn)]


def line_starts(text: str) -> list[int]:
    starts = [0]
    starts.extend(index + 1 for index, char in enumerate(text) if char == "\n")
    return starts


def line_start(text: str, cursor: int) -> int:
    return max(start for start in line_starts(text) if start <= cursor)


def line_end(text: str, cursor: int) -> int:
    end = text.find("\n", cursor)
    return len(text) if end < 0 else end


def wrap_text(text: str, width: int) -> list[str]:
    wrap_width = max(1, width)
    chunks = [
        wrapped
        for raw in text.splitlines() or [""]
        for wrapped in (textwrap.wrap(raw, wrap_width, replace_whitespace=False, drop_whitespace=False) or [""])
    ]
    return chunks or [""]


def clean_text(kind: str, content: str) -> str:
    text = content.replace("\r", "")
    return text.replace("**", "").replace("`", "") if kind in {"you", "bot", "thinking"} else text


def entry_lines(entry: Entry, width: int) -> list[tuple[str, Style]]:
    body = wrap_text(clean_text(entry.kind, entry.content), width if entry.kind in {"meta", "banner"} else width - 5)
    if entry.kind == "banner":
        return [(body[0], PREFIX_STYLES["banner"])]
    if entry.kind == "meta":
        return [(line, BODY_STYLES["meta"]) for line in body]
    prefix = f"{entry.kind}> "
    indent = " " * len(prefix)
    return [
        *((f"{prefix}{body[0]}", BODY_STYLES.get(entry.kind, Style())) for _ in body[:1]),
        *((f"{indent}{line}", BODY_STYLES.get(entry.kind, Style())) for line in body[1:]),
    ]


def transcript_lines(entries: list[Entry], width: int) -> list[tuple[str, Style]]:
    return [line for entry in entries for line in entry_lines(entry, width)]


def input_lines(text: str, width: int) -> list[str]:
    return input_layout(text, width, 0)[0]


def input_layout(text: str, width: int, cursor: int) -> tuple[list[str], tuple[int, int]]:
    prefix = "you> "
    first_width = max(1, width - len(prefix))
    later_width = max(1, width)
    rows = [""]
    row = col = 0
    cursor_pos = (len(prefix), 0)

    def cap(index: int) -> int:
        return first_width if index == 0 else later_width

    for index, char in enumerate(text):
        if index == cursor:
            cursor_pos = (col + (len(prefix) if row == 0 else 0), row)
        if char == "\n":
            rows.append("")
            row += 1
            col = 0
            continue
        if col >= cap(row):
            rows.append("")
            row += 1
            col = 0
        rows[row] += char
        col += 1
    if cursor == len(text):
        cursor_pos = (col + (len(prefix) if row == 0 else 0), row)
    return [f"{prefix}{rows[0]}", *rows[1:]], cursor_pos


def paragraph(lines: list[tuple[str, Style]], rect: Rect, style: Style) -> Paragraph:
    widget = Paragraph.new_empty()
    widget.set_style(style).reserve_lines(rect.height)
    for text, line_style in lines:
        widget.append_line(text, line_style)
    return widget


def viewport_width(width: int) -> int:
    return max(1, width)


def transcript_view(lines: list[tuple[str, Style]], height: int, scroll_from_bottom: int) -> list[tuple[str, Style]]:
    max_scroll = max(0, len(lines) - height)
    scroll = max(0, min(max_scroll, scroll_from_bottom))
    start = max(0, len(lines) - height - scroll)
    return lines[start : start + height]


def render(term: TerminalLike, ui: ChatUi) -> None:
    width, height = term.size()
    status_height = 1
    input_display, cursor = input_layout(ui.input_buffer.text, width, ui.input_buffer.cursor)
    input_height = min(MAX_INPUT_LINES, max(1, len(input_display)))
    transcript_height = max(1, height - status_height - input_height)

    transcript_rect = Rect(0, 0, width, transcript_height)
    status_rect = Rect(0, transcript_height, width, status_height)
    input_rect = Rect(0, transcript_height + status_height, width, input_height)

    lines = transcript_lines(ui.runtime.display_entries(), viewport_width(width))
    visible = transcript_view(lines, transcript_height, ui.scroll_from_bottom)
    status = [(ui.status_line()[:width], STATUS_STYLE)]
    input_widget_lines = [(line, INPUT_STYLE) for line in input_display[-input_height:]]

    term.draw_frame(
        [
            DrawCmd.paragraph(paragraph(visible, transcript_rect, TRANSCRIPT_BG), transcript_rect),
            DrawCmd.paragraph(paragraph(status, status_rect, STATUS_STYLE), status_rect),
            DrawCmd.paragraph(paragraph(input_widget_lines, input_rect, INPUT_STYLE), input_rect),
        ]
    )
    term.set_cursor_position(cursor[0], input_rect.y + min(cursor[1], input_height - 1))
    term.show_cursor()


def is_ctrl(event: KeyEvt, char: str) -> bool:
    return bool(event.mods & KeyMods.CTRL) and event.code == KeyCode.Char and chr(event.ch).lower() == char


def scroll_step(height: int) -> int:
    return max(1, height // 2)


async def handle_key(ui: ChatUi, event: KeyEvt, transcript_height: int) -> bool:
    if is_ctrl(event, "c"):
        return False if not ui.runtime.interrupt() else True
    if is_ctrl(event, "j"):
        ui.input_buffer.insert("\n")
        return True
    if event.code == KeyCode.Enter:
        return await ui.runtime.submit(ui.input_buffer.take())
    if event.code == KeyCode.Backspace:
        ui.input_buffer.backspace()
        return True
    if event.code == KeyCode.Delete:
        ui.input_buffer.delete()
        return True
    if event.code == KeyCode.Left:
        ui.input_buffer.move(-1)
        return True
    if event.code == KeyCode.Right:
        ui.input_buffer.move(1)
        return True
    if event.code == KeyCode.Home:
        ui.input_buffer.home()
        return True
    if event.code == KeyCode.End:
        ui.input_buffer.end()
        return True
    if event.code == KeyCode.Up:
        if event.mods & KeyMods.ALT:
            ui.scroll_from_bottom += 1
        else:
            ui.input_buffer.move_vertical(-1)
        return True
    if event.code == KeyCode.Down:
        if event.mods & KeyMods.ALT:
            ui.scroll_from_bottom = max(0, ui.scroll_from_bottom - 1)
        else:
            ui.input_buffer.move_vertical(1)
        return True
    if event.code == KeyCode.PageUp:
        ui.scroll_from_bottom += scroll_step(transcript_height)
        return True
    if event.code == KeyCode.PageDown:
        ui.scroll_from_bottom = max(0, ui.scroll_from_bottom - scroll_step(transcript_height))
        return True
    if event.code == KeyCode.Char and event.ch:
        ui.input_buffer.insert(chr(event.ch))
    return True


async def handle_event(ui: ChatUi, event: Any, transcript_height: int) -> bool:
    if isinstance(event, KeyEvt):
        return await handle_key(ui, event, transcript_height)
    if isinstance(event, MouseEvt):
        if event.mouse_kind == MouseKind.ScrollUp:
            ui.scroll_from_bottom += 3
        if event.mouse_kind == MouseKind.ScrollDown:
            ui.scroll_from_bottom = max(0, ui.scroll_from_bottom - 3)
    return True


def build_chat_runtime(
    config: Config | None = None,
    name: str | None = None,
    console: Any | None = None,
    client: AsyncOpenAI | None = None,
) -> ChatRuntime:
    _ = console
    return ChatRuntime(config=config or build_config(), name=name, client=client)


def default_session_factory() -> AbstractContextManager[TerminalLike]:
    return terminal_session(raw=True, alt=True, clear=True)


async def run_chat(
    config: Config | None = None,
    name: str | None = None,
    session_factory: Any = default_session_factory,
) -> None:
    runtime = build_chat_runtime(config, name=name)
    await runtime.start()
    ui = ChatUi(runtime=runtime)
    try:
        with session_factory() as term:
            while True:
                render(term, ui)
                width, height = term.size()
                transcript_height = max(1, height - 1 - min(MAX_INPUT_LINES, max(1, len(input_lines(ui.input_buffer.text, width)))))
                event = await asyncio.to_thread(term.next_event_typed, TICK_MS)
                if event is not None and not await handle_event(ui, event, transcript_height):
                    break
    finally:
        await runtime.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="faltoochat")
    parser.add_argument("--name", help="optional session name")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        asyncio.run(run_chat(name=args.name))
    except KeyboardInterrupt:
        return 130
    return 0


def stream_text(kind: str, delta: str) -> str:
    return clean_text(kind, delta.replace("\n", " " if kind == "thinking" else "\n"))
