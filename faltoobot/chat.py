import argparse
import asyncio
import json
import subprocess
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
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


def item_entries(item: dict[str, Any]) -> list[tuple[str, str]]:
    if item.get("type") == "reasoning":
        return [
            ("thinking", text)
            for summary in item.get("summary", [])
            if isinstance(summary, dict)
            for text in [summary.get("text")]
            if isinstance(text, str) and text.strip()
        ]
    if text := tool_entry(item):
        return [("tool", text)]
    return []


def item_id(item: dict[str, Any]) -> str | None:
    value = item.get("id") or item.get("call_id")
    return value if isinstance(value, str) else None


def item_key(item: dict[str, Any]) -> str | None:
    return item_id(item) or tool_entry(item)


def turn_entries(turn: Turn) -> list[tuple[str, str]]:
    return [
        *(entry for item in turn.items for entry in item_entries(item)),
        (TURN_KIND.get(turn.role, "bot"), turn.content),
    ]


def history_entries(session: Session) -> list[tuple[str, str]]:
    return [entry for turn in session.messages for entry in turn_entries(turn)]


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
    return Group(render_line(kind, ""), Padding(Markdown(content), (0, 0, 0, 2)))


def rich_renderable(kind: str, content: str) -> Text | Group:
    if kind in RICH_KINDS and looks_like_markdown(content):
        return render_markdown_block(kind, content)
    return render_line(kind, content)


def stream_text(kind: str, delta: str) -> str:
    if kind != "thinking":
        return delta
    return delta.replace("**", "").replace("`", "").replace("\n", " ")


async def read_input(prompt: str) -> str:
    loop = asyncio.get_running_loop()
    future: asyncio.Future[str] = loop.create_future()
    fileno = sys.stdin.fileno()

    sys.stdout.write(prompt)
    sys.stdout.flush()

    def on_readable() -> None:
        if future.done():
            return
        line = sys.stdin.readline()
        if line == "":
            future.set_exception(EOFError())
            return
        future.set_result(line.rstrip("\n"))

    loop.add_reader(fileno, on_readable)
    try:
        return await future
    finally:
        loop.remove_reader(fileno)


@dataclass(slots=True)
class StreamState:
    active_kind: str | None = None
    saw_bot: bool = False
    saw_thinking: bool = False
    tool_keys: set[str] = field(default_factory=set)


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

    def cli_session(self, workspace: Path, name: str | None = None) -> Session:
        if name is None:
            existing = existing_cli_session(self.config.sessions_dir, workspace)
            if existing is not None:
                return existing
        return cli_session(self.config.sessions_dir, session_name(name), workspace=workspace)

    def start_client(self) -> None:
        if self.client is None:
            self.client = AsyncOpenAI(api_key=self.config.openai_api_key)
            self.own_client = True

    def write_entries(self, entries: list[tuple[str, str]]) -> None:
        for kind, content in entries:
            self.write(kind, content)

    async def start(self) -> None:
        if not self.config.openai_api_key:
            raise RuntimeError(f"openai.api_key is missing. Add it to {self.config.config_file}")
        workspace = Path.cwd()
        self.session = self.cli_session(workspace, self.name)
        self.start_client()
        session = self.require_session()
        self.write_entries(
            [
                ("banner", " faltoochat "),
                ("meta", f"session: {session.name} ({session.id})"),
                ("meta", f"workspace: {session.workspace}"),
                ("meta", help_text()),
                *history_entries(session),
            ]
        )

    async def close(self) -> None:
        await self.wait_until_idle()
        if self.client and self.own_client:
            await self.client.close()

    def write(self, kind: str, content: str) -> None:
        self.console.print(rich_renderable(kind, content))

    def write_status(self) -> None:
        self.console.print(Text(input_hint(self.config), style=STATUS_STYLE))

    def start_stream(self, kind: str) -> None:
        self.console.print(Text(f"{kind}> ", style=PREFIX_STYLES.get(kind, "bold")), end="")

    def append_stream(self, kind: str, text: str) -> None:
        if text:
            self.console.print(Text(text, style=BODY_STYLES.get(kind, "#eef3f9")), end="")

    def end_stream(self) -> None:
        self.console.print()

    def stream_delta(self, state: StreamState, kind: str, delta: str) -> None:
        if not delta:
            return
        if state.active_kind != kind:
            self.close_stream(state)
            self.start_stream(kind)
            state.active_kind = kind
        if kind == "bot":
            state.saw_bot = True
        elif kind == "thinking":
            state.saw_thinking = True
        self.append_stream(kind, stream_text(kind, delta))

    def close_stream(self, state: StreamState) -> None:
        if state.active_kind is None:
            return
        self.end_stream()
        state.active_kind = None

    def store_assistant_turn(self, result: dict[str, Any]) -> Turn:
        answer = result["text"]
        turn = Turn(
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
        return turn

    def render_assistant_turn(self, turn: Turn, state: StreamState) -> None:
        self.write_entries(
            [
                entry
                for item in turn.items
                if item_key(item) not in state.tool_keys
                for entry in item_entries(item)
                if state.saw_thinking is False or entry[0] != "thinking"
            ]
        )
        if not state.saw_bot:
            self.write("bot", turn.content)

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
                self.session = self.cli_session(session.workspace, default_session_name())
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
        state = StreamState()

        async def on_text_delta(delta: str) -> None:
            self.stream_delta(state, "bot", delta)

        async def on_reasoning_delta(delta: str) -> None:
            self.stream_delta(state, "thinking", delta)

        async def on_reasoning_done() -> None:
            if state.active_kind != "thinking":
                return
            self.close_stream(state)

        async def on_output_item(item: dict[str, Any]) -> None:
            if text := tool_entry(item):
                self.close_stream(state)
                self.write("tool", text)
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
            self.write("meta", "reply interrupted")
            return
        except Exception as exc:
            self.close_stream(state)
            self.write("error", str(exc))
            return
        finally:
            self.current_reply_task = None

        self.close_stream(state)
        self.render_assistant_turn(self.store_assistant_turn(result), state)


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
                if runtime.interrupt():
                    continue
                break
            if not await runtime.submit(prompt):
                break
    except KeyboardInterrupt:
        runtime.console.print()
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
