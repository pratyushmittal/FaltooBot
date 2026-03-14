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
    return [("class:toolbar", f" {status_text(config)}  Enter send  Ctrl+J newline ")]


def prompt_bindings() -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add("enter")
    def submit(event: Any) -> None:
        event.current_buffer.validate_and_handle()

    @bindings.add("c-j")
    @bindings.add("escape", "enter")
    def newline(event: Any) -> None:
        event.current_buffer.insert_text("\n")

    return bindings


def render_line(kind: str, content: str) -> Text:
    if kind == "meta":
        return Text(content, style="dim #8ea4bc")
    prefix_style = {
        "you": "bold #ffb347",
        "bot": "bold #76c7ff",
        "thinking": "bold #93a8bd",
        "error": "bold #ff7b72",
        "opened": "bold #8ea4bc",
    }.get(kind, "bold")
    body_style = {
        "you": "#fff4df",
        "bot": "#e8f0f8",
        "thinking": "#aab9c9",
        "error": "#ffd5cf",
        "opened": "#d7e3ef",
    }.get(kind, "#eef3f9")
    text = Text()
    text.append(f"{kind}> ", style=prefix_style)
    text.append(content, style=body_style)
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
    prefix_style = {
        "you": "class:prompt",
        "bot": "class:bot",
        "thinking": "class:thinking",
        "error": "class:error",
        "opened": "class:meta",
        "banner": "class:banner",
    }.get(kind, "class:prompt")
    body_style = {
        "you": "class:you",
        "bot": "class:bot_text",
        "thinking": "class:thinking_text",
        "error": "class:error_text",
        "opened": "class:meta",
        "banner": "class:banner_text",
    }.get(kind, "")
    prefix = f"{kind}> " if kind != "banner" else ""
    return [(prefix_style, prefix), (body_style, content)]


@dataclass(slots=True)
class ChatRuntime:
    config: Config
    name: str | None = None
    console: Console = field(default_factory=Console)
    writer: Callable[[StyleAndTextTuples], None] | None = None
    rich_writer: Callable[[str], None] | None = None
    client: AsyncOpenAI | None = None
    session: Session | None = None
    own_client: bool = False
    pending_prompts: deque[str] = field(default_factory=deque)
    processing_task: asyncio.Task[None] | None = None
    stream_start: Callable[[StyleAndTextTuples], None] | None = None
    stream_delta: Callable[[str], None] | None = None
    stream_end: Callable[[], None] | None = None

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
        self.write("banner", " faltoochat ")
        self.write("meta", f"session: {self.session.name} ({self.session.id})")
        self.write("meta", f"workspace: {self.session.workspace}")
        self.write("meta", help_text())
        for kind, content in history_entries(self.session):
            self.write(kind, content)

    async def close(self) -> None:
        await self.wait_until_idle()
        if self.client and self.own_client:
            await self.client.close()

    def write(self, kind: str, content: str) -> None:
        if self.rich_writer and kind in {"you", "bot", "thinking"}:
            self.rich_writer(render_ansi(kind, content))
            return
        if self.writer:
            self.writer(render_fragments(kind, content))
            return
        self.console.print(rich_renderable(kind, content))

    def write_stream_start(self, kind: str) -> None:
        fragments = render_fragments(kind, "")
        if self.stream_start:
            self.stream_start(fragments)
            return
        self.console.file.write(f"{kind}> ")

    def write_stream_delta(self, text: str) -> None:
        if self.stream_delta:
            self.stream_delta(text)
            return
        if text:
            self.console.file.write(text)

    def write_stream_end(self) -> None:
        if self.stream_end:
            self.stream_end()
            return
        self.console.file.write("\n")

    async def submit(self, prompt: str) -> bool:
        text = prompt.strip()
        if not text:
            return True
        self.write("you", text)
        if text == "/help":
            self.write("meta", help_text())
            return True
        if text == "/tree":
            if self.session:
                open_in_default_editor(self.session.messages_file)
                self.write("opened", str(self.session.messages_file))
            return True
        if text == "/reset":
            if self.session:
                self.session = cli_session(
                    self.config.sessions_dir,
                    session_name(None),
                    self.session.workspace,
                )
                self.write("meta", f"new session: {self.session.name} ({self.session.id})")
            return True
        if text == "/exit":
            return False
        self.pending_prompts.append(text)
        self.ensure_processing()
        return True

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

    async def handle_prompt(self, prompt: str) -> None:
        if not self.session or not self.client:
            raise RuntimeError("chat session is not ready")
        self.session = add_turn(self.session, "user", prompt)
        streamed = False

        async def on_text_delta(delta: str) -> None:
            nonlocal streamed
            if not delta:
                return
            if not streamed:
                self.write_stream_start("bot")
                streamed = True
            self.write_stream_delta(delta)

        try:
            result = await stream_reply(
                self.client,
                self.config,
                self.session,
                session_items(self.session),
                on_text_delta=on_text_delta,
            )
        except Exception as exc:
            self.write("error", str(exc))
            return
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
        if streamed:
            self.write_stream_end()
        else:
            for text in summary_lines(assistant_turn):
                self.write("thinking", text)
            self.write("bot", answer)


def build_chat_runtime(
    config: Config | None = None,
    name: str | None = None,
    console: Console | None = None,
    writer: Callable[[StyleAndTextTuples], None] | None = None,
    rich_writer: Callable[[str], None] | None = None,
    stream_start: Callable[[StyleAndTextTuples], None] | None = None,
    stream_delta: Callable[[str], None] | None = None,
    stream_end: Callable[[], None] | None = None,
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
    runtime = build_chat_runtime(
        config,
        name=name,
        writer=lambda fragments: print_formatted_text(FormattedText(fragments), style=PROMPT_STYLE),
        rich_writer=lambda text: print_formatted_text(ANSI(text)),
        stream_start=lambda fragments: print_formatted_text(
            FormattedText(fragments),
            style=PROMPT_STYLE,
            end="",
        ),
        stream_delta=lambda text: print_formatted_text(text, style=PROMPT_STYLE, end=""),
        stream_end=lambda: print_formatted_text("", style=PROMPT_STYLE),
    )
    prompt_session = PromptSession(erase_when_done=True)
    bindings = prompt_bindings()
    await runtime.start()
    try:
        with patch_stdout():
            while True:
                prompt = await prompt_session.prompt_async(
                    [("class:prompt", "you> ")],
                    style=PROMPT_STYLE,
                    multiline=True,
                    wrap_lines=True,
                    bottom_toolbar=lambda: prompt_toolbar(runtime.config),
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
