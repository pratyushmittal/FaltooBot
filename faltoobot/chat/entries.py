import json
import re
import shlex
from dataclasses import dataclass, field
from typing import Any

from rich.console import Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.text import Text

from faltoobot.store import Session, Turn

MARKDOWN_KINDS = frozenset({"bot", "thinking"})
TURN_KIND = {"user": "you", "assistant": "bot"}
MAX_TOOL_LINES = 8
BOLD_SPAN_RE = re.compile(r"\*\*(.+?)\*\*", re.S)
QUEUE_PREVIEW_CHARS = 75
SED_RANGE_RE = re.compile(r"(?P<start>\d+)(?:,(?P<end>\d+))?p$")
MIN_CD_PREFIX_PARTS = 4

RG_VALUE_FLAGS = frozenset(
    {
        "-A",
        "-B",
        "-C",
        "-E",
        "-M",
        "-g",
        "-m",
        "-t",
        "-T",
        "--after-context",
        "--before-context",
        "--colors",
        "--context",
        "--encoding",
        "--engine",
        "--glob",
        "--iglob",
        "--max-count",
        "--max-columns",
        "--path-separator",
        "--pre",
        "--pre-glob",
        "--regex-size-limit",
        "--sort",
        "--sortr",
        "--type",
        "--type-add",
        "--type-clear",
        "--type-not",
    }
)


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


def shell_command_summary(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    if not parts:
        return command
    parts = strip_shell_prefix(parts)
    if not parts:
        return command
    if parts[0] == "sed":
        return sed_command_summary(parts) or command
    if parts[0] == "rg":
        return rg_command_summary(parts) or command
    return command


def strip_shell_prefix(parts: list[str]) -> list[str]:
    if len(parts) < MIN_CD_PREFIX_PARTS or parts[0] != "cd":
        return parts
    for separator in ("&&", ";"):
        if separator in parts[2:]:
            return parts[parts.index(separator) + 1 :]
    return parts


def sed_command_summary(parts: list[str]) -> str | None:
    script = None
    filename = None
    index = 1
    while index < len(parts):
        part = parts[index]
        if part == "--":
            index += 1
            break
        if part.startswith("-"):
            index += 2 if part in {"-e", "-f"} else 1
            continue
        script = part
        index += 1
        break
    if script is None or index >= len(parts):
        return None
    filename = parts[index]
    if not filename:
        return None
    if not (match := SED_RANGE_RE.fullmatch(script)):
        return None
    start = match.group("start")
    end = match.group("end") or start
    return f"reading {filename} {start} to {end}"


def rg_command_summary(parts: list[str]) -> str | None:
    pattern = None
    locations: list[str] = []
    index = 1
    while index < len(parts):
        part = parts[index]
        if part == "--":
            index += 1
            break
        if part.startswith("-"):
            if part in RG_VALUE_FLAGS:
                index += 2
            else:
                index += 1
            continue
        pattern = part
        index += 1
        break
    if pattern is None:
        return None
    while index < len(parts):
        part = parts[index]
        if not part.startswith("-"):
            locations.append(part)
        index += 1
    location = " ".join(locations) or "."
    return f"searching for {pattern} in {location}"


def shell_tool_lines(item: dict[str, Any], item_type: str) -> list[str] | None:
    action = item.get("action")
    if item_type == "shell_call":
        commands = action.get("commands") if isinstance(action, dict) else None
        if isinstance(commands, list):
            return [
                "shell",
                *(shell_command_summary(str(command)) for command in commands),
            ]
        return None
    if item_type not in {"local_shell_call", "function_shell_call"}:
        return None
    command = action.get("command") if isinstance(action, dict) else None
    if not isinstance(command, list):
        return None
    return ["shell", shell_command_summary(" ".join(str(part) for part in command))]


def function_tool_lines(item: dict[str, Any], item_type: str) -> list[str] | None:
    if item_type != "function_call":
        return None
    name = item.get("name")
    arguments = item.get("arguments")
    if not isinstance(name, str):
        return None
    lines = [name]
    if isinstance(arguments, str) and arguments.strip():
        try:
            payload = json.dumps(json.loads(arguments), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            payload = arguments
        lines.extend(payload.splitlines())
    return lines


def search_tool_lines(item: dict[str, Any], item_type: str) -> list[str] | None:
    if item_type not in {
        "web_search_call",
        "function_web_search",
        "tool_search_call",
        "file_search_call",
    }:
        return None
    action = item.get("action")
    query = action.get("query") if isinstance(action, dict) else item.get("query")
    if isinstance(query, str) and query.strip():
        return ["web search", query]
    return ["web search"]


def generic_tool_lines(item: dict[str, Any], item_type: str) -> list[str] | None:
    if not item_type.endswith("_call") or item_type.endswith("_output"):
        return None
    details = item.get("name") or item.get("call_id") or item.get("id")
    label = item_type.replace("_", " ")
    return [label, str(details)] if details else [label]


def tool_lines(item: dict[str, Any]) -> list[str]:
    item_type = item.get("type")
    if not isinstance(item_type, str):
        return []
    for lines in (
        shell_tool_lines(item, item_type),
        function_tool_lines(item, item_type),
        search_tool_lines(item, item_type),
        generic_tool_lines(item, item_type),
    ):
        if lines is not None:
            return lines
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
        *(
            [Entry(TURN_KIND.get(turn.role, "bot"), turn.content)]
            if turn.content
            else []
        ),
    ]


def history_entries(session: Session) -> list[Entry]:
    return [entry for turn in session.messages for entry in turn_entries(turn)]


def visible_content(kind: str, content: str) -> str:
    if kind != "thinking":
        return content
    matches = [
        match.strip() for match in BOLD_SPAN_RE.findall(content) if match.strip()
    ]
    if not matches:
        return content
    return "\n".join(f"**{match}**" for match in matches)


def looks_like_markdown(content: str) -> bool:
    return any(
        token in content
        for token in ("**", "__", "`", "[", "](", "\n#", "\n-", "\n1. ")
    )


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
