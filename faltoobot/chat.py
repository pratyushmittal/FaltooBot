import argparse
import asyncio
import base64
import json
import mimetypes
import os
import re
import select
import shlex
import subprocess
import sys
import termios
import time
import tty
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from openai import AsyncOpenAI
from PIL import Image
from rich.console import Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult, ScreenStackError
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Markdown as TextualMarkdown
from textual.widgets import Static, TextArea
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
    sync_assistant_turn,
)

MARKDOWN_KINDS = frozenset({"bot", "thinking"})
TURN_KIND = {"user": "you", "assistant": "bot"}
MAX_TOOL_LINES = 8
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})
MARKDOWN_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]+)\)")
BOLD_SPAN_RE = re.compile(r"\*\*(.+?)\*\*", re.S)
PROMPT_ARG_RE = re.compile(r"\$(\d+)\b")
PROMPT_RANGE_RE = re.compile(r"\$\{@:(?P<start>\d+)(?::(?P<end>\d+))?\}")
MAX_IMAGE_WIDTH = 1600
MAX_IMAGE_HEIGHT = 1200
QUEUE_PREVIEW_CHARS = 75
COMMANDS = (
    ("/help", "show help"),
    ("/tree", "open messages file"),
    ("/reset", "start a new session"),
    ("/exit", "exit chat"),
)
QUEUE_SHORTCUTS = (
    "Tab queue/input",
    "↑/↓ select",
    "Enter edit",
    "Space pause",
    "Del remove",
    "Shift+↑/↓ move",
)
SCROLL_SETTLE_DELAYS = (0.01, 0.05)
STARTUP_SCROLL_INTERVAL = 0.1
STARTUP_SCROLL_DURATION = 2.0


MarkdownFence.highlight = classmethod(lambda cls, code, language: Content(code))  # type: ignore[assignment]


def as_session_path(source: str, workspace: Path) -> Path | None:
    def existing(path: Path) -> Path | None:
        try:
            return path if path.exists() else None
        except OSError:
            return None

    value = source.strip().strip('"').strip("'")
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme == "file":
        path = Path(unquote(parsed.path))
    elif parsed.scheme:
        return None
    else:
        raw = Path(os.path.expanduser(value))
        path = raw if raw.is_absolute() else workspace / raw
        if existing(path) is None and "\\" in value:
            try:
                parts = shlex.split(value)
            except ValueError:
                parts = []
            if len(parts) == 1:
                raw = Path(os.path.expanduser(parts[0]))
                path = raw if raw.is_absolute() else workspace / raw
    if existing(path) is None:
        return None
    try:
        return path.resolve()
    except OSError:
        return None



def is_image_path(path: Path) -> bool:
    mime_type, _ = mimetypes.guess_type(path.name)
    return path.is_file() and (
        (mime_type or "").startswith("image/") or path.suffix.lower() in IMAGE_EXTENSIONS
    )


def is_image_url(source: str) -> bool:
    value = source.strip()
    if value.startswith("data:image/"):
        return True
    parsed = urlparse(value)
    return (
        parsed.scheme in {"http", "https"} and Path(parsed.path).suffix.lower() in IMAGE_EXTENSIONS
    )


def image_markdown(path: Path, alt: str | None = None) -> str:
    return f"![{alt or path.name}]({path.as_uri()})"


def paste_image_text(text: str, workspace: Path) -> str:
    lines = text.splitlines()
    if not lines:
        return text
    return "\n".join(
        f"![image]({line.strip()})"
        if is_image_url(line)
        else image_markdown(path)
        if (path := as_session_path(line, workspace)) and is_image_path(path)
        else line
        for line in lines
    )


def image_label(source: str, alt: str, workspace: Path) -> str:
    if alt.strip():
        return alt.strip()
    if path := as_session_path(source, workspace):
        return path.name
    parsed = urlparse(source.strip())
    name = Path(unquote(parsed.path)).name
    return name or "image"


def display_prompt(prompt: str, workspace: Path) -> str:
    text = MARKDOWN_IMAGE_RE.sub(
        lambda match: f"[image: {image_label(match.group('src'), match.group('alt'), workspace)}]",
        prompt,
    ).strip()
    return text or "[image]"


def clipboard_image_bytes() -> bytes | None:
    if sys.platform != "darwin":
        return None
    script = """
import AppKit
let pasteboard = NSPasteboard.general
if let image = NSImage(pasteboard: pasteboard),
   let tiff = image.tiffRepresentation,
   let bitmap = NSBitmapImageRep(data: tiff),
   let png = bitmap.representation(using: .png, properties: [:]) {
    print(png.base64EncodedString())
}
""".strip()
    try:
        result = subprocess.run(
            ["swift", "-"],
            input=script,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    data = result.stdout.strip()
    return base64.b64decode(data) if result.returncode == 0 and data else None


def save_clipboard_image(session: Session) -> Path | None:
    if not (data := clipboard_image_bytes()):
        return None
    path = (
        session.root
        / "attachments"
        / f"clipboard-{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S-%f')}.png"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def fitted_image_size(width: int, height: int) -> tuple[int, int]:
    scale = min(MAX_IMAGE_WIDTH / width, MAX_IMAGE_HEIGHT / height, 1)
    return max(1, int(width * scale)), max(1, int(height * scale))


def resized_image_upload(path: Path) -> BytesIO | None:
    with Image.open(path) as image:
        width, height = image.size
        target = fitted_image_size(width, height)
        if target == (width, height):
            return None
        resized = image.resize(target, Image.Resampling.LANCZOS)
        buffer = BytesIO()
        format_name = "JPEG" if image.format in {"JPEG", "JPG"} else "PNG"
        suffix = ".jpg" if format_name == "JPEG" else ".png"
        resized.save(buffer, format=format_name)
    buffer.seek(0)
    buffer.name = f"{path.stem}-{target[0]}x{target[1]}{suffix}"  # type: ignore[attr-defined]
    return buffer


async def input_image_part(client: AsyncOpenAI, workspace: Path, source: str) -> dict[str, Any]:
    value = source.strip()
    if is_image_url(value):
        return {"type": "input_image", "image_url": value, "detail": "auto"}
    path = as_session_path(value, workspace)
    if path is None or not is_image_path(path):
        raise ValueError(f"Image not found: {source}")
    if upload := resized_image_upload(path):
        uploaded = await client.files.create(file=upload, purpose="vision")
    else:
        with path.open("rb") as handle:
            uploaded = await client.files.create(file=handle, purpose="vision")
    return {"type": "input_image", "file_id": uploaded.id, "detail": "auto"}


async def prompt_message_item(
    client: AsyncOpenAI,
    workspace: Path,
    prompt: str,
) -> tuple[str, dict[str, Any] | None]:
    if not MARKDOWN_IMAGE_RE.search(prompt):
        return prompt, None
    content: list[dict[str, Any]] = []
    cursor = 0
    for match in MARKDOWN_IMAGE_RE.finditer(prompt):
        prefix = prompt[cursor : match.start()]
        if prefix:
            content.append({"type": "input_text", "text": prefix})
        content.append(await input_image_part(client, workspace, match.group("src")))
        cursor = match.end()
    suffix = prompt[cursor:]
    if suffix:
        content.append({"type": "input_text", "text": suffix})
    return display_prompt(prompt, workspace), {
        "type": "message",
        "role": "user",
        "content": [
            part
            for part in content
            if part.get("type") == "input_image" or str(part.get("text") or "")
        ],
    }


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


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    command: str
    detail: str
    body: str


def prompts_dir(root: Path) -> Path:
    return root / "prompts"


def prompt_command_name(path: Path) -> str | None:
    name = path.stem.strip()
    if not name or any(char.isspace() for char in name):
        return None
    return f"/{name}"


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        return {}, text
    meta = {}
    for line in parts[0].splitlines()[1:]:
        key, sep, value = line.partition(":")
        if not sep:
            continue
        cleaned = value.strip()
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
            cleaned = cleaned[1:-1]
        meta[key.strip()] = cleaned
    return meta, parts[1]


def prompt_detail(name: str, body: str, meta: dict[str, str]) -> str:
    if description := meta.get("description"):
        return description
    for line in body.splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned.lstrip("#").strip()
    return f"saved prompt {name}"


def prompt_templates(root: Path) -> tuple[PromptTemplate, ...]:
    directory = prompts_dir(root)
    if not directory.exists():
        return ()
    reserved = {command for command, _ in COMMANDS}
    templates: list[PromptTemplate] = []
    for path in sorted(directory.glob("*.md")):
        if not path.is_file():
            continue
        command = prompt_command_name(path)
        if command is None or command in reserved:
            continue
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        meta, body = split_frontmatter(text)
        body = body.strip()
        if not body:
            continue
        templates.append(PromptTemplate(command, prompt_detail(path.stem, body, meta), body))
    return tuple(templates)


def slash_commands(root: Path) -> tuple[tuple[str, str], ...]:
    return COMMANDS + tuple((template.command, template.detail) for template in prompt_templates(root))


def split_saved_prompt(text: str) -> tuple[str, list[str]]:
    command, _, remainder = text.strip().partition(" ")
    args_text = remainder.strip()
    if not args_text:
        return command, []
    try:
        return command, shlex.split(args_text)
    except ValueError:
        return command, args_text.split()


def expand_prompt_body(body: str, args: list[str]) -> str:
    all_args = " ".join(args)

    def replace_range(match: re.Match[str]) -> str:
        start = max(0, int(match.group("start")) - 1)
        end_text = match.group("end")
        end = int(end_text) if end_text else len(args)
        return " ".join(args[start:end])

    expanded = body.replace("$ARGUMENTS", all_args).replace("$@", all_args)
    expanded = PROMPT_RANGE_RE.sub(replace_range, expanded)
    return PROMPT_ARG_RE.sub(
        lambda match: args[index] if (index := int(match.group(1)) - 1) in range(len(args)) else "",
        expanded,
    ).strip()


def expand_saved_prompt(root: Path, text: str) -> str | None:
    command, args = split_saved_prompt(text)
    for template in prompt_templates(root):
        if template.command == command:
            return expand_prompt_body(template.body, args)
    return None


def default_session_name() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def help_text() -> str:
    names = ", ".join(name for name, _ in COMMANDS)
    return f"Commands: {names}. Saved prompts: ~/.faltoobot/prompts/*.md. Ctrl+V image"


def slash_query(text: str) -> str | None:
    query = text.strip()
    if not query.startswith("/") or any(char.isspace() for char in query):
        return None
    return query


def slash_suggestions(
    text: str,
    commands: tuple[tuple[str, str], ...] = COMMANDS,
) -> tuple[tuple[str, str], ...]:
    query = slash_query(text)
    if query is None:
        return ()
    if query == "/":
        return commands
    return tuple(item for item in commands if item[0].startswith(query))


def session_name(name: str | None) -> str:
    return f"CLI {name or default_session_name()}"


def status_text(config: Config) -> str:
    model = f"{config.openai_model} (fast)" if config.openai_fast else config.openai_model
    return f"model: {model}  thinking: {config.openai_thinking}"


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
    parts = [status_text(config)]
    if replying:
        parts.append("replying")
    if queued:
        parts.append(f"queued {queued}")
    if queued or queue_selected:
        parts.extend(QUEUE_SHORTCUTS)
    parts.append("Ctrl+V paste/image")
    parts.append("Ctrl+C interrupt")
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
    if item_type in {
        "web_search_call",
        "function_web_search",
        "tool_search_call",
        "file_search_call",
    }:
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
        *([Entry(TURN_KIND.get(turn.role, "bot"), turn.content)] if turn.content else []),
    ]


def history_entries(session: Session) -> list[Entry]:
    return [entry for turn in session.messages for entry in turn_entries(turn)]


def visible_content(kind: str, content: str) -> str:
    if kind != "thinking":
        return content
    matches = [match.strip() for match in BOLD_SPAN_RE.findall(content) if match.strip()]
    if not matches:
        return content
    return "\n".join(f"**{match}**" for match in matches)


def looks_like_markdown(content: str) -> bool:
    return any(token in content for token in ("**", "__", "`", "[", "](", "\n#", "\n-", "\n1. "))


def uses_markdown(kind: str, content: str) -> bool:
    visible = visible_content(kind, content)
    return kind in MARKDOWN_KINDS and (looks_like_markdown(visible) or "\n" in visible)


def rich_renderable(kind: str, content: str) -> Text | Group:
    visible = visible_content(kind, content)
    if kind == "banner":
        return Text(visible, style="bold")
    if kind == "meta":
        return Text(visible, style="dim")
    if uses_markdown(kind, content):
        return Group(Padding(Markdown(visible), 0))
    return Text(visible)


def queue_preview(content: str) -> str:
    preview = " ".join(part.strip() for part in content.splitlines() if part.strip())
    return (preview or content.strip())[:QUEUE_PREVIEW_CHARS]


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
        self.pending_prompts = [
            QueuedPrompt(prompt.content, True) for prompt in self.session.queued_prompts
        ]
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
        text = expand_saved_prompt(self.config.root, text) or text
        if self.can_start_prompt_now():
            self.start_prompt_now(text)
            return True
        self.enqueue_prompt(text)
        self.notify()
        self.ensure_processing()
        return True

    def slash_commands(self) -> tuple[tuple[str, str], ...]:
        return slash_commands(self.config.root)

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

    def can_start_prompt_now(self) -> bool:
        return (
            self.current_reply_task is None
            and not self.pending_prompts
            and (self.processing_task is None or self.processing_task.done())
        )

    def start_prompt_now(self, prompt: str) -> None:
        display_text = display_prompt(prompt, self.require_session().workspace)
        self.append_entry("you", display_text, notify=False)
        self.notify()
        self.processing_task = asyncio.create_task(self.process_now(prompt, display_text))

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

    async def process_now(self, prompt: str, display_text: str) -> None:
        await self.handle_prompt(prompt, display_text=display_text, already_rendered=True)
        if self.pending_prompts:
            await self.process_pending()
        else:
            self.notify()

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

    def replace_last_entry(self, kind: str, content: str) -> None:
        for index in range(len(self.entries) - 1, -1, -1):
            if self.entries[index].kind == kind:
                self.entries[index] = Entry(kind, content)
                return
        self.entries.append(Entry(kind, content))

    def replace_last_bot_entry(self, content: str) -> None:
        self.replace_last_entry("bot", content)

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

    def sync_assistant_progress(
        self,
        content: str,
        items: list[dict[str, Any]],
        *,
        usage: dict[str, Any] | None = None,
        instructions: str | None = None,
    ) -> Turn | None:
        if not items and not content:
            return None
        self.session = sync_assistant_turn(
            self.require_session(),
            content,
            items=items,
            usage=usage,
            instructions=instructions,
        )
        return self.require_session().messages[-1]

    def store_assistant_turn(self, result: dict[str, Any]) -> Turn:
        turn = self.sync_assistant_progress(
            result["text"],
            list(result["output_items"]),
            usage=result["usage"],
            instructions=result["instructions"],
        )
        if turn is None:
            raise RuntimeError("assistant reply was empty")
        return turn

    def render_assistant_turn(self, turn: Turn, state: StreamState) -> None:
        self.entries.extend(
            entry
            for item in turn.items
            if item_key(item) not in state.tool_keys
            for entry in item_entries(item)
            if not (state.saw_thinking and entry.kind == "thinking")
        )
        if turn.content:
            if state.saw_bot:
                self.replace_last_bot_entry(turn.content)
            else:
                self.entries.append(Entry("bot", turn.content))
        self.notify()

    async def handle_prompt(
        self,
        prompt: str,
        *,
        display_text: str | None = None,
        already_rendered: bool = False,
    ) -> None:
        session = self.require_session()
        optimistic_text = display_text or display_prompt(prompt, session.workspace)
        if not already_rendered:
            self.append_entry("you", optimistic_text)
        display_text, message_item = await prompt_message_item(
            self.require_client(),
            session.workspace,
            prompt,
        )
        session = add_turn(
            session,
            "user",
            display_text,
            items=[message_item] if message_item else None,
        )
        self.session = session
        if already_rendered and display_text != optimistic_text:
            self.replace_last_entry("you", display_text)
            self.notify()
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

        async def on_stream_end(items: list[dict[str, Any]], text: str) -> None:
            self.sync_assistant_progress(text, items)

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
                on_stream_end=on_stream_end,
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

    def workspace(self) -> Path:
        runtime = getattr(self.app, "runtime", None)
        session = getattr(runtime, "session", None)
        return session.workspace if isinstance(session, Session) else Path.cwd()

    def insert_text(self, value: str) -> None:
        if result := self._replace_via_keyboard(value, *self.selection):
            self.move_cursor(result.end_location)
            self.focus()

    async def _on_paste(self, event: events.Paste) -> None:
        if self.read_only:
            return
        event.stop()
        event.prevent_default()
        if getattr(self, "_skip_next_paste", False):
            self._skip_next_paste = False
            return
        self.insert_text(paste_image_text(event.text, self.workspace()))

    def action_paste(self) -> None:
        if self.read_only:
            return
        runtime = getattr(self.app, "runtime", None)
        session = getattr(runtime, "session", None)
        if isinstance(session, Session) and (path := save_clipboard_image(session)):
            self._skip_next_paste = True
            self.insert_text(image_markdown(path))

    def on_key(self, event: Any) -> None:
        handler = getattr(self.app, "handle_composer_key", None)
        if callable(handler) and handler(event.key):
            event.prevent_default()
            event.stop()
            return
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.post_message(self.Submitted(self.text))
            return
        if event.key == "tab":
            event.prevent_default()
            event.stop()
            self.insert("\t")
            return
        if event.key in {"shift+enter", "ctrl+j"}:
            event.prevent_default()
            event.stop()
            self.insert("\n")



class SlashCommandItem(Horizontal):
    class Picked(Message):
        def __init__(self, command: str) -> None:
            self.command = command
            super().__init__()

    def __init__(self, command: str, detail: str) -> None:
        self.command = command
        self.detail = detail
        super().__init__(classes="slash-command-item")

    def compose(self) -> ComposeResult:
        yield Static(Text(self.command), classes="slash-command-name")
        yield Static(Text(self.detail), classes="slash-command-detail")

    def on_click(self, event: Any) -> None:
        event.stop()
        self.post_message(self.Picked(self.command))


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

    def __init__(self, index: int, prompt: QueuedPrompt) -> None:
        self.index = index
        self.content = queue_preview(prompt.content)
        self.paused = prompt.paused
        super().__init__(classes="queue-item")

    def marker(self) -> str:
        return "□" if self.paused else "☑︎"

    def select(self, selected: bool) -> None:
        self.set_class(selected, "-selected")

    def compose(self) -> ComposeResult:
        yield Static(
            Text(f"{self.marker()} {self.content}", overflow="ellipsis", no_wrap=True),
            classes="queue-text",
        )

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
    EntryBlock,
    LiveMarkdownBlock {
        width: 1fr;
        max-width: 80;
        min-width: 0;
        height: auto;
        margin: 0 0 1 0;
    }

    EntryBlock > .body,
    LiveMarkdownBlock > .body {
        width: 1fr;
        min-width: 0;
        height: auto;
        padding: 0 1;
        background: transparent;
        color: $text;
        overflow-x: hidden;
    }

    EntryBlock.entry-you > .body,
    LiveMarkdownBlock.entry-you > .body {
        background: $primary 8%;
    }

    EntryBlock.entry-bot > .body,
    LiveMarkdownBlock.entry-bot > .body {
        background: $surface;
    }

    EntryBlock.entry-thinking > .body,
    LiveMarkdownBlock.entry-thinking > .body {
        color: $text-muted;
        background: $surface-active;
    }

    EntryBlock.entry-tool > .body,
    LiveMarkdownBlock.entry-tool > .body {
        color: $secondary;
        background: $secondary 8%;
    }

    EntryBlock.entry-error > .body,
    LiveMarkdownBlock.entry-error > .body {
        color: $error;
        background: $error 8%;
    }

    EntryBlock.entry-opened > .body,
    LiveMarkdownBlock.entry-opened > .body {
        color: $accent;
        background: $accent 8%;
    }

    EntryBlock.entry-banner > .body,
    EntryBlock.entry-meta > .body,
    LiveMarkdownBlock.entry-banner > .body,
    LiveMarkdownBlock.entry-meta > .body {
        background: transparent;
    }

    EntryBlock.entry-banner > .body,
    LiveMarkdownBlock.entry-banner > .body {
        color: $warning;
        text-style: bold;
    }

    EntryBlock.entry-meta > .body,
    LiveMarkdownBlock.entry-meta > .body {
        color: $text-disabled;
    }
    """

    def __init__(self, entry: Entry) -> None:
        self.entry = entry
        super().__init__(classes=entry_class(entry.kind))

    def compose(self) -> ComposeResult:
        kind = self.entry.kind
        content = visible_content(self.entry.kind, self.entry.content)
        if kind in {"banner", "meta"} or not self.uses_markdown():
            yield Static(Text(content), id="body", classes="body")
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
            self.query_one("#body", TextualMarkdown).update(visible_content(entry.kind, entry.content))
            return True
        self.query_one("#body", Static).update(Text(visible_content(entry.kind, entry.content)))
        return True


class LiveMarkdownBlock(Vertical):
    DEFAULT_CSS = EntryBlock.DEFAULT_CSS

    def __init__(self, entry: Entry) -> None:
        self.entry = entry
        super().__init__(classes=entry_class(entry.kind))

    def compose(self) -> ComposeResult:
        yield Static(Text(visible_content(self.entry.kind, self.entry.content)), id="body", classes="body")

    def set_entry(self, entry: Entry) -> bool:
        if entry.kind != self.entry.kind or entry.kind not in {"bot", "thinking"}:
            return False
        self.entry = entry
        self.query_one("#body", Static).update(Text(visible_content(entry.kind, entry.content)))
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
        overflow-x: hidden;
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
        padding: 0;
    }

    #commands {
        height: auto;
        layout: vertical;
        background: $background;
        border: none;
        padding: 0 0 1 0;
    }

    .slash-command-item {
        height: 1;
        layout: horizontal;
        align: left middle;
        padding: 0 1;
        background: $background;
        color: $text;
    }

    .slash-command-name {
        width: 10;
        text-style: bold;
        color: $accent;
    }

    .slash-command-detail {
        width: 1fr;
        color: $text-muted;
    }

    .queue-item {
        height: 1;
        align: left middle;
        padding: 0 1;
        background: $background;
        color: $text;
        margin: 0;
    }

    .queue-item.-selected {
        background: $primary 18%;
    }

    .queue-item.-selected .queue-text {
        text-style: bold;
    }

    .queue-text {
        width: 1fr;
        height: 1;
        color: $text;
    }

    #footer {
        width: 1fr;
        max-width: 120;
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
        color: $text-disabled;
        text-style: none;
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
        self._snapshot: tuple[tuple[str, str, bool], ...] = ()
        self._blocks: list[EntryBlock | LiveMarkdownBlock] = []
        self._stream_block: EntryBlock | LiveMarkdownBlock | None = None
        self._queue_snapshot: tuple[tuple[str, bool], ...] = ()
        self._command_snapshot: tuple[tuple[str, str], ...] = ()
        self._dismissed_slash_query: str | None = None
        self._queue_selected_snapshot: int | None = None
        self._queue_selected: int | None = None
        self._queue_drag_index: int | None = None
        self._startup_scroll: Any | None = None
        self._follow_transcript = False

    def make_live_block(self, entry: Entry) -> LiveMarkdownBlock:
        return LiveMarkdownBlock(entry)

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            yield VerticalScroll(id="transcript")
            with Center():
                with Vertical(id="footer"):
                    yield Vertical(id="queue")
                    yield Vertical(id="commands")
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

    def commands(self) -> Vertical:
        return self.query_one("#commands", Vertical)

    def status(self) -> Static:
        return self.query_one("#status", Static)

    def focus_composer(self) -> None:
        try:
            self.composer().focus()
        except (NoMatches, ScreenStackError):
            return

    def scroll_transcript_end(self, *, settle: bool = False) -> None:
        self._scroll_transcript_end_after_refresh()
        self.call_after_refresh(self._scroll_transcript_end_after_refresh)
        if settle:
            for delay in SCROLL_SETTLE_DELAYS:
                self.set_timer(delay, self._scroll_transcript_end_after_refresh)

    def _scroll_transcript_end_after_refresh(self) -> None:
        try:
            self.transcript().scroll_end(animate=False, immediate=True)
        except NoMatches:
            return

    def stop_startup_scroll(self) -> None:
        if self._startup_scroll is None:
            return
        self._startup_scroll.stop()
        self._startup_scroll = None

    def stop_following_transcript(self) -> None:
        self._follow_transcript = False
        self.stop_startup_scroll()


    def pin_transcript_during_startup(self) -> None:
        self.scroll_transcript_end(settle=True)
        self.stop_startup_scroll()
        self._startup_scroll = self.set_interval(STARTUP_SCROLL_INTERVAL, self.scroll_transcript_end)
        self.set_timer(STARTUP_SCROLL_DURATION, self.stop_startup_scroll)

    async def on_mount(self) -> None:
        self.runtime.set_notifier(self.sync_view)
        await self.runtime.start()
        self.sync_view(force=True)
        self.pin_transcript_during_startup()
        self.call_after_refresh(self.focus_composer)

    async def on_unmount(self) -> None:
        self.stop_startup_scroll()
        await self.runtime.close()

    async def on_composer_submitted(self, message: Composer.Submitted) -> None:
        composer = self.composer()
        composer.load_text("")
        self._follow_transcript = True
        if not await self.runtime.submit(message.value):
            self.runtime.pending_prompts.clear()
            self.runtime.interrupt()
            self.exit()
            return
        self.sync_view()

    def refresh_ui(self) -> None:
        try:
            self.sync_view()
        except NoMatches:
            return

    def sync_view(self, force: bool = False) -> None:
        queue_layout_changed = self.refresh_queue(force=force)
        self.refresh_commands(force=force)
        self.refresh_status()
        self.refresh_transcript(force=force or queue_layout_changed)

    def refresh_commands(self, *, force: bool = False) -> None:
        query = slash_query(self.composer().text)
        if self._dismissed_slash_query is not None and query != self._dismissed_slash_query:
            self._dismissed_slash_query = None
        commands = self.runtime.slash_commands()
        suggestions = (
            ()
            if query is not None and query == self._dismissed_slash_query
            else slash_suggestions(self.composer().text, commands)
        )
        if not force and suggestions == self._command_snapshot:
            return
        commands = self.commands()
        commands.remove_children()
        items = [SlashCommandItem(command, detail) for command, detail in suggestions]
        if items:
            commands.mount(*items)
            commands.display = True
        else:
            commands.display = False
        self._command_snapshot = suggestions

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
        elif self._queue_selected is not None and self._queue_selected >= len(queued):
            self._queue_selected = len(queued) - 1

    def refresh_queue(self, *, force: bool = False) -> bool:
        queued = self.runtime.queued_prompt_items()
        queue_snapshot = tuple((prompt.content, prompt.paused) for prompt in queued)
        self.normalize_queue_selection()
        selection_changed = self._queue_selected != self._queue_selected_snapshot
        layout_changed = queue_snapshot != self._queue_snapshot
        if not force and not layout_changed and not selection_changed:
            return False
        queue = self.queue()
        if not force and selection_changed and not layout_changed:
            for item in queue.query("QueueItem"):
                item.select(item.index == self._queue_selected)
            self._queue_selected_snapshot = self._queue_selected
            return False
        queue.remove_children()
        items = [QueueItem(index, prompt) for index, prompt in enumerate(queued)]
        for item in items:
            item.select(item.index == self._queue_selected)
        if items:
            queue.mount(*items)
            queue.display = True
        else:
            queue.display = False
        self._queue_snapshot = queue_snapshot
        self._queue_selected_snapshot = self._queue_selected
        return layout_changed or force

    def refresh_transcript(self, *, force: bool = False) -> None:
        entries = list(self.runtime.entries)
        live = self.runtime.live_entry
        snapshot = (
            tuple((entry.kind, entry.content, False) for entry in entries)
            + (((live.kind, live.content, True),) if live else ())
        )
        snapshot_changed = snapshot != self._snapshot
        if not force and not snapshot_changed:
            return

        transcript = self.transcript()
        at_end = transcript.is_vertical_scroll_end
        if not at_end:
            self.stop_startup_scroll()
        previous_scroll = transcript.scroll_y
        had_live = self._stream_block is not None or live is not None
        previous_rendered = tuple((block.entry.kind, block.entry.content) for block in self._blocks)
        rendered = tuple((entry.kind, entry.content) for entry in entries)
        append_only = rendered[: len(self._blocks)] == previous_rendered
        tail_updated = (
            len(rendered) == len(previous_rendered)
            and bool(rendered)
            and rendered[:-1] == previous_rendered[:-1]
            and rendered[-1][0] == previous_rendered[-1][0]
            and rendered[-1][1] != previous_rendered[-1][1]
        )

        if force or not append_only:
            transcript.remove_children()
            self._blocks = []
            self._stream_block = None
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
            if self._stream_block is not None:
                final_index = len(self._blocks)
                if final_index < len(entries):
                    final_entry = entries[final_index]
                    if self._stream_block.entry.kind == final_entry.kind:
                        committed = EntryBlock(final_entry)
                        self._stream_block.remove()
                        transcript.mount(committed)
                        self._blocks.append(committed)
                        self._stream_block = None
                if self._stream_block is not None:
                    self._stream_block.remove()
                    self._stream_block = None
        elif self._stream_block is None:
            self._stream_block = self.make_live_block(live)
            transcript.mount(self._stream_block)
        elif not self._stream_block.set_entry(live):
            self._stream_block.remove()
            self._stream_block = self.make_live_block(live)
            transcript.mount(self._stream_block)

        should_scroll_end = force or self._follow_transcript or at_end
        if should_scroll_end:
            self.scroll_transcript_end(settle=had_live or tail_updated)
        else:
            transcript.scroll_to(y=previous_scroll, animate=False, immediate=True)
            self.call_after_refresh(
                lambda: transcript.scroll_to(y=previous_scroll, animate=False, immediate=True)
            )
        self._snapshot = snapshot

    @on(TextArea.Changed, "#composer")
    def on_composer_changed(self, _: TextArea.Changed) -> None:
        self.refresh_commands()

    def dismiss_slash_commands(self) -> bool:
        query = slash_query(self.composer().text)
        if query is None or not self._command_snapshot:
            return False
        self._dismissed_slash_query = query
        self.refresh_commands(force=True)
        return True

    @on(SlashCommandItem.Picked)
    def on_slash_command_item_picked(self, message: SlashCommandItem.Picked) -> None:
        self._dismissed_slash_query = None
        composer = self.composer()
        composer.load_text(message.command)
        self.focus_composer()
        self.refresh_commands(force=True)

    @on(events.MouseScrollUp, "#transcript")
    def on_transcript_mouse_scroll_up(self, event: Any) -> None:
        event.stop()
        self.stop_following_transcript()

    @on(events.MouseDown, "#transcript")
    def on_transcript_mouse_down(self, event: Any) -> None:
        self.stop_following_transcript()

    def action_interrupt_or_quit(self) -> None:
        if not self.runtime.interrupt():
            self.exit()

    def queue_selection_after_change(self, index: int) -> int | None:
        total = len(self.runtime.pending_prompts)
        return min(index, total - 1) if total else None

    def edit_queue(self, index: int) -> None:
        if (prompt := self.runtime.remove_prompt(index)) is None:
            return
        self._queue_selected = self.queue_selection_after_change(index)
        composer = self.composer()
        composer.load_text(prompt)
        self.focus_composer()
        self.sync_view()

    def delete_queue(self, index: int) -> None:
        if self.runtime.remove_prompt(index) is None:
            return
        self._queue_selected = self.queue_selection_after_change(index)
        self.sync_view()

    def move_queue(self, index: int, target: int) -> None:
        if (new_index := self.runtime.move_prompt(index, target)) is None:
            return
        self._queue_selected = new_index
        self.sync_view()

    def move_queue_selection(self, delta: int) -> bool:
        total = len(self.runtime.pending_prompts)
        if not total or self._queue_selected is None:
            return False
        if delta < 0:
            self._queue_selected = max(0, self._queue_selected - 1)
        else:
            self._queue_selected = min(total - 1, self._queue_selected + 1)
        self.focus_composer()
        self.sync_view()
        return True

    def toggle_queue_focus(self) -> bool:
        total = len(self.runtime.pending_prompts)
        if not total:
            return True
        self._queue_selected = None if self._queue_selected is not None else total - 1
        self.focus_composer()
        self.sync_view()
        return True

    def handle_composer_key(self, key: str) -> bool:
        if key == "escape":
            return self.dismiss_slash_commands()
        if key == "tab":
            return self.toggle_queue_focus()
        if self._queue_selected is None:
            return False
        if key == "up":
            return self.move_queue_selection(-1)
        if key == "down":
            return self.move_queue_selection(1)
        if key == "enter":
            self.action_edit_selected_queue()
            return True
        if key in {"delete", "backspace"}:
            self.action_delete_selected_queue()
            return True
        if key == "space":
            self.action_toggle_selected_queue_pause()
            return True
        if key == "shift+up":
            self.action_move_selected_queue_up()
            return True
        if key == "shift+down":
            self.action_move_selected_queue_down()
            return True
        return False

    @on(QueueItem.Picked)
    def on_queue_item_picked(self, message: QueueItem.Picked) -> None:
        if self._queue_drag_index is not None and self._queue_drag_index != message.index:
            return
        self._queue_selected = message.index
        self.focus_composer()
        self.sync_view()

    @on(QueueItem.DragStart)
    def on_queue_item_drag_start(self, message: QueueItem.DragStart) -> None:
        self._queue_drag_index = message.index
        self._queue_selected = message.index
        self.focus_composer()
        self.sync_view()

    @on(QueueItem.DragFinish)
    def on_queue_item_drag_finish(self, message: QueueItem.DragFinish) -> None:
        if self._queue_drag_index is None:
            return
        source = self._queue_drag_index
        self._queue_drag_index = None
        if source == message.index:
            return
        self.move_queue(source, message.index)

    def action_edit_selected_queue(self) -> None:
        if self._queue_selected is not None:
            self.edit_queue(self._queue_selected)

    def toggle_queue_pause(self, index: int) -> None:
        if (paused := self.runtime.toggle_prompt_paused(index)) is not None:
            self._queue_selected = index
            if not paused:
                self.runtime.ensure_processing()
            self.sync_view()

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
    await build_chat_app(
        config=config, name=name, terminal_dark=terminal_background_dark()
    ).run_async()


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
