import argparse
import asyncio
import io
import math
import shutil
import subprocess
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
from rich.console import Console, Group
from rich.constrain import Constrain
from rich.markdown import Markdown
from rich.padding import Padding
from rich.text import Text

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

RICH_KINDS = frozenset({"you", "bot", "thinking"})
PREFIX_STYLES = {
    "you": "bold #ffb347",
    "bot": "bold #76c7ff",
    "thinking": "bold #93a8bd",
    "error": "bold #ff7b72",
    "opened": "bold #8ea4bc",
}
BODY_STYLES = {
    "you": "#fff4df",
    "bot": "#e8f0f8",
    "thinking": "#aab9c9",
    "error": "#ffd5cf",
    "opened": "#d7e3ef",
}
STATUS_STYLE = "bold #8ea4bc on #0b1520"
MESSAGE_WIDTH = 80
STREAM_INLINE_STYLES = (
    ("`", "bold #ffe7c2 on #243244"),
    ("**", "bold"),
)


def default_session_name() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def help_text() -> str:
    return "Commands: /help, /tree, /reset, /exit"


def session_name(name: str | None) -> str:
    return f"CLI {name or default_session_name()}"


def status_text(config: Config) -> str:
    return f"model: {config.openai_model}  thinking: {config.openai_thinking}"


def input_hint(config: Config) -> str:
    return f"{status_text(config)}  Enter send  Ctrl+C interrupt"


def open_in_default_editor(path: Path) -> None:
    command = ["open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
    subprocess.Popen(command)  # noqa: S603


def summary_lines(turn: Turn) -> list[str]:
    return [
        text
        for item in turn.items
        if item.get("type") == "reasoning"
        for summary in item.get("summary", [])
        if isinstance(summary, dict)
        for text in [summary.get("text")]
        if isinstance(text, str) and text.strip()
    ]


def history_entries(session: Session) -> list[tuple[str, str]]:
    return [
        entry
        for turn in session.messages
        for entry in [
            *(("thinking", text) for text in summary_lines(turn)),
            ("you" if turn.role == "user" else "bot", turn.content),
        ]
    ]


def render_line(kind: str, content: str) -> Text:
    if kind == "meta":
        return Text(content, style="dim #8ea4bc")
    text = Text()
    text.append(f"{kind}> ", style=PREFIX_STYLES.get(kind, "bold"))
    text.append(content, style=BODY_STYLES.get(kind, "#eef3f9"))
    return text


def merge_styles(kind: str, extra: str = "") -> str:
    base = BODY_STYLES.get(kind, "#eef3f9")
    return f"{base} {extra}".strip()


def stream_renderable(kind: str, content: str) -> Text:
    if kind not in RICH_KINDS:
        return render_line(kind, content)
    text = Text()
    text.append(f"{kind}> ", style=PREFIX_STYLES[kind])
    index = 0
    while index < len(content):
        marker = next((token for token, _ in STREAM_INLINE_STYLES if content.startswith(token, index)), None)
        if marker is not None:
            end = content.find(marker, index + len(marker))
            if end != -1:
                marker_style = next(style for token, style in STREAM_INLINE_STYLES if token == marker)
                text.append(content[index + len(marker) : end], style=merge_styles(kind, marker_style))
                index = end + len(marker)
                continue
            text.append(marker, style=merge_styles(kind))
            index += len(marker)
            continue
        next_marker = min(
            (
                position
                for token, _ in STREAM_INLINE_STYLES
                for position in [content.find(token, index)]
                if position != -1
            ),
            default=len(content),
        )
        text.append(content[index:next_marker], style=merge_styles(kind))
        index = next_marker
    return text


def looks_like_markdown(content: str) -> bool:
    return any(token in content for token in ("**", "__", "`", "[", "](", "\n#", "\n-", "\n1. "))


def render_markdown_block(kind: str, content: str) -> Group:
    return Group(
        render_line(kind, ""),
        Padding(Constrain(Markdown(content), width=MESSAGE_WIDTH - 2), (0, 0, 0, 2)),
    )


def rich_renderable(kind: str, content: str) -> Text | Group | Constrain:
    if kind in RICH_KINDS and looks_like_markdown(content):
        return render_markdown_block(kind, content)
    renderable = render_line(kind, content)
    return Constrain(renderable, width=MESSAGE_WIDTH) if kind in RICH_KINDS else renderable


def render_ansi(kind: str, content: str, *, streaming: bool = False) -> str:
    capture = io.StringIO()
    width = shutil.get_terminal_size((100, 20)).columns
    renderable = stream_renderable(kind, content) if streaming else rich_renderable(kind, content)
    if streaming and kind in RICH_KINDS:
        renderable = Constrain(renderable, width=MESSAGE_WIDTH)
    Console(file=capture, force_terminal=True, color_system="truecolor", width=width).print(renderable)
    return capture.getvalue()


def stream_text(kind: str, delta: str) -> str:
    if kind != "thinking":
        return delta
    return delta.replace("**", "").replace("`", "").replace("\n", " ")


def wrapped_line_count(text: str, width: int) -> int:
    return sum(max(1, math.ceil(max(0, len(line)) / max(1, width))) for line in text.splitlines() or [""])


def streamed_line_count(kind: str, content: str, width: int) -> int:
    return wrapped_line_count(f"{kind}> {content}", width)


async def read_input(prompt: str) -> str:
    return await asyncio.to_thread(input, prompt)


@dataclass(slots=True)
class ChatRuntime:
    config: Config
    name: str | None = None
    console: Console = field(default_factory=Console)
    client: AsyncOpenAI | None = None
    session: Session | None = None
    own_client: bool = False
    pending_prompts: deque[str] = field(default_factory=deque)
    processing_task: asyncio.Task[None] | None = None
    current_reply_task: asyncio.Task[dict[str, Any]] | None = None

    def require_session(self) -> Session:
        if self.session is None:
            raise RuntimeError("chat session is not ready")
        return self.session

    def require_client(self) -> AsyncOpenAI:
        if self.client is None:
            raise RuntimeError("chat session is not ready")
        return self.client

    async def start(self) -> None:
        if not self.config.openai_api_key:
            raise RuntimeError(f"openai.api_key is missing. Add it to {self.config.config_file}")
        workspace = Path.cwd()
        self.session = (
            existing_cli_session(self.config.sessions_dir, workspace) if self.name is None else None
        ) or cli_session(self.config.sessions_dir, session_name(self.name), workspace=workspace)
        if self.client is None:
            self.client = AsyncOpenAI(api_key=self.config.openai_api_key)
            self.own_client = True
        session = self.require_session()
        self.write("banner", " faltoochat ")
        self.write("meta", f"session: {session.name} ({session.id})")
        self.write("meta", f"workspace: {session.workspace}")
        self.write("meta", help_text())
        for kind, content in history_entries(session):
            self.write(kind, content)

    async def close(self) -> None:
        await self.wait_until_idle()
        if self.client and self.own_client:
            await self.client.close()

    def write(self, kind: str, content: str) -> None:
        self.console.print(rich_renderable(kind, content))

    def write_status(self) -> None:
        self.console.print(Text(input_hint(self.config), style=STATUS_STYLE))

    def start_stream(self, kind: str) -> None:
        self.console.file.write(f"{kind}> ")
        self.console.file.flush()

    def append_stream(self, text: str) -> None:
        if text:
            self.console.file.write(text)
            self.console.file.flush()

    def end_stream(self) -> None:
        self.console.file.write("\n")
        self.console.file.flush()

    def replace_streamed_output(self, blocks: list[tuple[str, str]]) -> None:
        if not blocks or not self.console.is_terminal:
            return
        width = max(1, min(self.console.width, MESSAGE_WIDTH))
        lines = sum(streamed_line_count(kind, content, width) for kind, content in blocks)
        self.console.file.write("".join("\x1b[1A\x1b[2K\r" for _ in range(lines)))
        self.console.file.flush()

    def redraw_stream(self, kind: str, content: str, lines: int) -> int:
        if not self.console.is_terminal:
            return lines
        if lines:
            self.console.file.write("".join("\x1b[1A\x1b[2K\r" for _ in range(lines)))
        self.console.file.write(render_ansi(kind, content, streaming=True))
        self.console.file.flush()
        return streamed_line_count(kind, content, max(1, min(self.console.width, MESSAGE_WIDTH)))

    async def submit(self, prompt: str) -> bool:
        text = prompt.strip()
        if not text:
            return True
        command_result = await self.handle_command(text)
        if command_result is not None:
            return command_result
        self.pending_prompts.append(text)
        self.ensure_processing()
        return True

    async def handle_command(self, text: str) -> bool | None:
        match text:
            case "/help":
                self.write("meta", help_text())
                return True
            case "/tree":
                session = self.require_session()
                open_in_default_editor(session.messages_file)
                self.write("opened", str(session.messages_file))
                return True
            case "/reset":
                session = self.require_session()
                self.session = cli_session(
                    self.config.sessions_dir,
                    session_name(None),
                    session.workspace,
                )
                new_session = self.require_session()
                self.write("meta", f"new session: {new_session.name} ({new_session.id})")
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
        if self.processing_task:
            await self.processing_task
            self.processing_task = None

    def interrupt(self) -> bool:
        if not self.current_reply_task or self.current_reply_task.done():
            return False
        self.current_reply_task.cancel()
        return True

    async def handle_prompt(self, prompt: str) -> None:
        session = add_turn(self.require_session(), "user", prompt)
        self.session = session
        active_stream: str | None = None
        active_text = ""
        active_lines = 0
        streamed_blocks: list[tuple[str, str]] = []
        streamed_bot = False
        streamed_thinking = False

        def start_stream(kind: str) -> None:
            nonlocal active_stream, active_text, active_lines
            if active_stream == kind:
                return
            if active_stream:
                streamed_blocks.append((active_stream, active_text))
                if not self.console.is_terminal:
                    self.end_stream()
            if not self.console.is_terminal:
                self.start_stream(kind)
            active_stream = kind
            active_text = ""
            active_lines = 0

        async def on_text_delta(delta: str) -> None:
            nonlocal streamed_bot, active_lines, active_text
            if not delta:
                return
            start_stream("bot")
            streamed_bot = True
            chunk = stream_text("bot", delta)
            active_text += chunk
            if self.console.is_terminal:
                active_lines = self.redraw_stream("bot", active_text, active_lines)
                return
            self.append_stream(chunk)

        async def on_reasoning_delta(delta: str) -> None:
            nonlocal streamed_thinking, active_lines, active_text
            if not delta:
                return
            start_stream("thinking")
            streamed_thinking = True
            chunk = stream_text("thinking", delta)
            active_text += chunk
            if self.console.is_terminal:
                active_lines = self.redraw_stream("thinking", active_text, active_lines)
                return
            self.append_stream(chunk)

        async def on_reasoning_done() -> None:
            nonlocal active_lines, active_stream, active_text
            if active_stream != "thinking":
                return
            streamed_blocks.append(("thinking", active_text))
            if not self.console.is_terminal:
                self.end_stream()
            active_stream = None
            active_lines = 0
            active_text = ""

        self.current_reply_task = asyncio.create_task(
            stream_reply(
                self.require_client(),
                self.config,
                session,
                session_items(session),
                on_text_delta=on_text_delta,
                on_reasoning_delta=on_reasoning_delta,
                on_reasoning_done=on_reasoning_done,
            )
        )
        try:
            result = await self.current_reply_task
        except asyncio.CancelledError:
            if active_stream:
                streamed_blocks.append((active_stream, active_text))
                if not self.console.is_terminal:
                    self.end_stream()
            self.write("meta", "reply interrupted")
            return
        except Exception as exc:
            if active_stream:
                streamed_blocks.append((active_stream, active_text))
                if not self.console.is_terminal:
                    self.end_stream()
            self.write("error", str(exc))
            return
        finally:
            self.current_reply_task = None

        answer = result["text"]
        assistant_turn = Turn(
            role="assistant",
            content=answer,
            created_at="",
            items=tuple(result["output_items"]),
        )
        self.session = add_turn(
            self.require_session(),
            "assistant",
            answer,
            items=result["output_items"],
            usage=result["usage"],
            instructions=result["instructions"],
        )
        if active_stream:
            streamed_blocks.append((active_stream, active_text))
            if not self.console.is_terminal:
                self.end_stream()
        if streamed_blocks:
            self.replace_streamed_output(streamed_blocks)
        if not streamed_thinking:
            for text in summary_lines(assistant_turn):
                self.write("thinking", text)
        elif streamed_blocks and self.console.is_terminal:
            for text in summary_lines(assistant_turn):
                self.write("thinking", text)
        if not streamed_bot:
            self.write("bot", answer)
        elif streamed_blocks and self.console.is_terminal:
            self.write("bot", answer)


def build_chat_runtime(
    config: Config | None = None,
    name: str | None = None,
    console: Console | None = None,
    client: AsyncOpenAI | None = None,
) -> ChatRuntime:
    return ChatRuntime(
        config=config or build_config(),
        name=name,
        console=console or Console(),
        client=client,
    )


async def run_chat(config: Config | None = None, name: str | None = None) -> None:
    runtime = build_chat_runtime(config, name=name)
    await runtime.start()
    runtime.write_status()
    try:
        while True:
            try:
                prompt = await read_input("you> ")
            except EOFError:
                runtime.console.print()
                break
            except KeyboardInterrupt:
                runtime.console.print()
                break
            if not await runtime.submit(prompt):
                break
            while runtime.processing_task:
                try:
                    await runtime.wait_until_idle()
                except KeyboardInterrupt:
                    if not runtime.interrupt():
                        raise
    except KeyboardInterrupt:
        runtime.console.print()
    finally:
        await runtime.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="faltoochat")
    parser.add_argument("--name", help="optional session name")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(run_chat(name=args.name))
