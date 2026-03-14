import argparse
import asyncio
import io
import shutil
import subprocess
import sys
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
from prompt_toolkit import PromptSession
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.application.current import get_app_or_none
from prompt_toolkit.formatted_text import ANSI, FormattedText, StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import print_formatted_text
from prompt_toolkit.styles import Style
from rich.console import Console, Group
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
FRAGMENT_PREFIX_STYLES = {
    "you": "class:prompt",
    "bot": "class:bot",
    "thinking": "class:thinking",
    "error": "class:error",
    "opened": "class:meta",
    "banner": "class:banner",
}
FRAGMENT_BODY_STYLES = {
    "you": "class:you",
    "bot": "class:bot_text",
    "thinking": "class:thinking_text",
    "error": "class:error_text",
    "opened": "class:meta",
    "banner": "class:banner_text",
}

PROMPT_STYLE = Style.from_dict(
    {
        "banner": "bold #08111b bg:#ffb347",
        "banner_text": "bold #08111b bg:#ffb347",
        "meta": "#8ea4bc",
        "prompt": "bold #ffb347",
        "you": "#fff4df",
        "bot": "bold #76c7ff",
        "bot_text": "#e8f0f8",
        "thinking": "bold #93a8bd",
        "thinking_text": "#aab9c9",
        "error": "bold #ff7b72",
        "error_text": "#ffd5cf",
        "continuation": "#516a86",
        "toolbar": "fg:#8ea4bc bg:#0b1520",
    }
)


def default_session_name() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def help_text() -> str:
    return "Commands: /help, /tree, /reset, /exit"


def session_name(name: str | None) -> str:
    return f"CLI {name or default_session_name()}"


def status_text(config: Config) -> str:
    return f"model: {config.openai_model}  thinking: {config.openai_thinking}"


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


def prompt_toolbar(config: Config) -> StyleAndTextTuples:
    return [("class:toolbar", f" {status_text(config)}  Enter send  Ctrl+J newline  Ctrl+Q interrupt ")]


def prompt_message(config: Config, live_kind: str | None = None, live_text: str = "") -> StyleAndTextTuples:
    fragments = [*prompt_toolbar(config), ("", "\n")]
    if live_kind and live_text:
        fragments.extend(render_fragments(live_kind, live_text))
        fragments.append(("", "\n"))
    fragments.append(("class:prompt", "you> "))
    return fragments


def prompt_bindings(on_interrupt: Callable[[], None] | None = None) -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add("enter")
    def submit(event: Any) -> None:
        event.current_buffer.validate_and_handle()

    @bindings.add("c-j")
    @bindings.add("escape", "enter")
    def newline(event: Any) -> None:
        event.current_buffer.insert_text("\n")

    @bindings.add("c-q")
    def interrupt(event: Any) -> None:
        del event
        if on_interrupt:
            on_interrupt()

    return bindings


def render_line(kind: str, content: str) -> Text:
    if kind == "meta":
        return Text(content, style="dim #8ea4bc")
    text = Text()
    text.append(f"{kind}> ", style=PREFIX_STYLES.get(kind, "bold"))
    text.append(content, style=BODY_STYLES.get(kind, "#eef3f9"))
    return text


def looks_like_markdown(content: str) -> bool:
    return any(token in content for token in ("**", "__", "`", "[", "](", "\n#", "\n-", "\n1. "))


def render_markdown_block(kind: str, content: str) -> Group:
    prefix = render_line(kind, "")
    body = Padding(Markdown(content), (0, 0, 0, 2))
    return Group(prefix, body)


def rich_renderable(kind: str, content: str) -> Text | Group:
    if kind in {"you", "bot", "thinking"} and looks_like_markdown(content):
        return render_markdown_block(kind, content)
    return render_line(kind, content)


def render_ansi(kind: str, content: str) -> str:
    capture = io.StringIO()
    width = shutil.get_terminal_size((100, 20)).columns
    Console(file=capture, force_terminal=True, color_system="truecolor", width=width).print(
        rich_renderable(kind, content),
        end="",
    )
    return capture.getvalue()


def render_fragments(kind: str, content: str) -> StyleAndTextTuples:
    if kind == "meta":
        return [("class:meta", content)]
    prefix = f"{kind}> " if kind != "banner" else ""
    return [
        (FRAGMENT_PREFIX_STYLES.get(kind, "class:prompt"), prefix),
        (FRAGMENT_BODY_STYLES.get(kind, ""), content),
    ]


def stream_text(kind: str, delta: str) -> str:
    if kind != "thinking":
        return delta
    return delta.replace("**", "").replace("`", "").replace("\n", " ")


async def emit_callback(callback: Callable[..., Any] | None, *args: Any) -> None:
    if not callback:
        return
    result = callback(*args)
    if asyncio.iscoroutine(result):
        await result


@dataclass(slots=True)
class ChatRuntime:
    config: Config
    name: str | None = None
    console: Console = field(default_factory=Console)
    writer: Callable[[StyleAndTextTuples], Any] | None = None
    rich_writer: Callable[[str], Any] | None = None
    client: AsyncOpenAI | None = None
    session: Session | None = None
    own_client: bool = False
    pending_prompts: deque[str] = field(default_factory=deque)
    processing_task: asyncio.Task[None] | None = None
    current_reply_task: asyncio.Task[dict[str, Any]] | None = None
    stream_start: Callable[[str], Any] | None = None
    stream_delta: Callable[[str], Any] | None = None
    stream_end: Callable[[], Any] | None = None
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

    def prompt_message(self) -> StyleAndTextTuples:
        return prompt_message(self.config, self.live_kind, self.live_text)

    def refresh_prompt(self) -> None:
        if app := get_app_or_none():
            app.invalidate()

    def start_live(self, kind: str) -> None:
        self.live_kind = kind
        self.live_text = ""
        self.refresh_prompt()

    def append_live(self, text: str) -> None:
        self.live_text += text
        self.refresh_prompt()

    def end_live(self) -> None:
        self.live_kind = None
        self.live_text = ""
        self.refresh_prompt()

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
        await self.write("banner", " faltoochat ")
        await self.write("meta", f"session: {session.name} ({session.id})")
        await self.write("meta", f"workspace: {session.workspace}")
        await self.write("meta", help_text())
        for kind, content in history_entries(session):
            await self.write(kind, content)

    async def close(self) -> None:
        await self.wait_until_idle()
        if self.client and self.own_client:
            await self.client.close()

    async def write(self, kind: str, content: str) -> None:
        if self.rich_writer and kind in RICH_KINDS:
            await emit_callback(self.rich_writer, render_ansi(kind, content))
            return
        if self.writer:
            await emit_callback(self.writer, render_fragments(kind, content))
            return
        self.console.print(rich_renderable(kind, content))

    async def write_stream_start(self, kind: str) -> None:
        if self.stream_start:
            await emit_callback(self.stream_start, kind)
            return
        self.console.file.write(f"{kind}> ")

    async def write_stream_delta(self, text: str) -> None:
        if self.stream_delta:
            await emit_callback(self.stream_delta, text)
            return
        if text:
            self.console.file.write(text)

    async def write_stream_end(self) -> None:
        if self.stream_end:
            await emit_callback(self.stream_end)
            return
        self.console.file.write("\n")

    async def submit(self, prompt: str) -> bool:
        text = prompt.strip()
        if not text:
            return True
        await self.write("you", text)
        command_result = await self.handle_command(text)
        if command_result is not None:
            return command_result
        self.pending_prompts.append(text)
        self.ensure_processing()
        return True

    async def handle_command(self, text: str) -> bool | None:
        match text:
            case "/help":
                await self.write("meta", help_text())
                return True
            case "/tree":
                session = self.require_session()
                open_in_default_editor(session.messages_file)
                await self.write("opened", str(session.messages_file))
                return True
            case "/reset":
                session = self.require_session()
                self.session = cli_session(
                    self.config.sessions_dir,
                    session_name(None),
                    session.workspace,
                )
                new_session = self.require_session()
                await self.write("meta", f"new session: {new_session.name} ({new_session.id})")
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
        streamed_bot = False
        streamed_thinking = False

        async def start_stream(kind: str) -> None:
            nonlocal active_stream
            if active_stream == kind:
                return
            if active_stream:
                await self.write_stream_end()
            await self.write_stream_start(kind)
            active_stream = kind

        async def on_text_delta(delta: str) -> None:
            nonlocal streamed_bot
            if not delta:
                return
            await start_stream("bot")
            streamed_bot = True
            await self.write_stream_delta(stream_text("bot", delta))

        async def on_reasoning_delta(delta: str) -> None:
            nonlocal streamed_thinking
            if not delta:
                return
            await start_stream("thinking")
            streamed_thinking = True
            await self.write_stream_delta(stream_text("thinking", delta))

        async def on_reasoning_done() -> None:
            nonlocal active_stream
            if active_stream != "thinking":
                return
            await self.write_stream_end()
            active_stream = None

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
                await self.write_stream_end()
            await self.write("meta", "reply interrupted")
            return
        except Exception as exc:
            await self.write("error", str(exc))
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
            self.session,
            "assistant",
            answer,
            items=result["output_items"],
            usage=result["usage"],
            instructions=result["instructions"],
        )
        if active_stream:
            await self.write_stream_end()
        if not streamed_thinking:
            for text in summary_lines(assistant_turn):
                await self.write("thinking", text)
        if not streamed_bot:
            await self.write("bot", answer)


def build_chat_runtime(
    config: Config | None = None,
    name: str | None = None,
    console: Console | None = None,
    writer: Callable[[StyleAndTextTuples], Any] | None = None,
    rich_writer: Callable[[str], Any] | None = None,
    stream_start: Callable[[str], Any] | None = None,
    stream_delta: Callable[[str], Any] | None = None,
    stream_end: Callable[[], Any] | None = None,
    client: AsyncOpenAI | None = None,
) -> ChatRuntime:
    return ChatRuntime(
        config or build_config(),
        name=name,
        console=console or Console(),
        writer=writer,
        rich_writer=rich_writer,
        stream_start=stream_start,
        stream_delta=stream_delta,
        stream_end=stream_end,
        client=client,
    )


async def run_chat(config: Config | None = None, name: str | None = None) -> None:
    async def write_fragments(fragments: StyleAndTextTuples) -> None:
        await run_in_terminal(lambda: print_formatted_text(FormattedText(fragments), style=PROMPT_STYLE))

    async def write_rich(text: str) -> None:
        await run_in_terminal(lambda: print_formatted_text(ANSI(text)))

    runtime = build_chat_runtime(
        config,
        name=name,
        writer=write_fragments,
        rich_writer=write_rich,
    )
    runtime.stream_start = lambda kind: runtime.start_live(kind)
    runtime.stream_delta = lambda text: runtime.append_live(text)
    runtime.stream_end = lambda: runtime.end_live()
    prompt_session = PromptSession(erase_when_done=True)
    bindings = prompt_bindings(runtime.interrupt)
    await runtime.start()
    try:
        with patch_stdout():
            while True:
                prompt = await prompt_session.prompt_async(
                    getattr(runtime, "prompt_message", lambda: prompt_message(runtime.config)),
                    style=PROMPT_STYLE,
                    multiline=True,
                    wrap_lines=True,
                    prompt_continuation=lambda width, _line, _wrap: [("class:continuation", "... ")],
                    key_bindings=bindings,
                )
                if not await runtime.submit(prompt):
                    break
    except (EOFError, KeyboardInterrupt):
        runtime.console.print()
    finally:
        await runtime.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="faltoochat")
    parser.add_argument("--name", help="optional session name")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    import asyncio

    asyncio.run(run_chat(name=args.name))
