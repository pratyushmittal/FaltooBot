import json
import re
import shlex
from typing import Any

from faltoobot.gpt_utils import MessageItem

SED_RANGE_RE = re.compile(r"(?P<start>\d+)(?:,(?P<end>\d+))?p$")
MIN_CD_PREFIX_PARTS = 4
BOLD_SPAN_RE = re.compile(r"\*\*(.+?)\*\*", re.S)

SHELL_COMMAND_SEPARATOR = "\n\n<!-- shell-command -->\n\n"

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


def _shell_command_summary(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    if not parts:
        return command
    parts = _strip_shell_prefix(parts)
    if not parts:
        return command
    if parts[0] == "sed":
        return _sed_command_summary(parts) or command
    if parts[0] == "rg":
        return _rg_command_summary(parts) or command
    return command


def _strip_shell_prefix(parts: list[str]) -> list[str]:
    if len(parts) < MIN_CD_PREFIX_PARTS or parts[0] != "cd":
        return parts
    for separator in ("&&", ";"):
        if separator in parts[2:]:
            return parts[parts.index(separator) + 1 :]
    return parts


def _sed_command_summary(parts: list[str]) -> str | None:
    script = None
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


def _rg_command_summary(parts: list[str]) -> str | None:
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


def _content_text(parts: list[Any]) -> str:
    values: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if text := str(part.get("text") or "").strip():
            values.append(text)
            continue
        if part.get("type") == "input_image":
            values.append("[image]")
    return "\n".join(values)


def _get_text(value: Any) -> str:
    match value:
        case str(text):
            return text.strip()
        case list(parts):
            return _content_text(parts)
        case _:
            return ""


def visible_thinking_text(text: str) -> str:
    matches = [match.strip() for match in BOLD_SPAN_RE.findall(text) if match.strip()]
    if not matches:
        return text
    return "\n".join(f"**{match}**" for match in matches)


def _clip_lines(text: str, max_lines: int = 5) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join([*lines[: max_lines - 1], "..."])


def _tool_call_text(name: str, arguments: str) -> str:
    parsed_arguments: dict[str, Any] | None = None
    if arguments.strip():
        try:
            parsed_arguments = json.loads(arguments)
            arguments = json.dumps(parsed_arguments, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            pass
        if name == "run_shell_call" and isinstance(parsed_arguments, dict):
            command = parsed_arguments.get("command")
            command_summary = parsed_arguments.get("command_summary")
            if isinstance(command_summary, str) and command_summary.strip():
                return (
                    f"**Shell:** {command_summary.strip()}{SHELL_COMMAND_SEPARATOR}{command.strip()}"
                    if isinstance(command, str) and command.strip()
                    else f"**Shell:** {command_summary.strip()}"
                )
            if isinstance(command, str) and command.strip():
                summary = _shell_command_summary(command)
                if summary != command:
                    return _clip_lines(summary)
        return _clip_lines(f"{name}\n{arguments}")
    return name


def _tool_text(item: MessageItem) -> str | None:
    match item:
        case {"type": "function_call", "name": str(name), "arguments": str(arguments)}:
            return _tool_call_text(name, arguments)
        case {
            "type": "web_search_call",
            "action": {"query": str(query)},
        }:
            return f"web search\n{query.strip()}" if query.strip() else "web search"
        case {"type": str(item_type)} if item_type.endswith("_call"):
            return item_type.replace("_", " ")
        case _:
            return None


def get_item_text(item: MessageItem) -> tuple[str, str] | None:
    if text := _tool_text(item):
        return text, "tool"
    match item:
        case {"type": "message", "role": "user", "content": content}:
            text = _get_text(content)
            classes = (
                "user review-comments"
                if text.startswith("# Comments in code review")
                else "user"
            )
            return (text, classes) if text else None
        case {"type": "message", "content": content}:
            text = _get_text(content)
            return (text, "answer") if text else None
        case {"type": "reasoning", "summary": summary}:
            text = visible_thinking_text(_get_text(summary))
            return (text, "thinking") if text else None
        case {"type": "function_call_output"}:
            return None
        case _:
            return None
