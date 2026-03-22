import json
from typing import Any

from faltoobot.gpt_utils import MessageItem


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


def _clip_lines(text: str, max_lines: int = 5) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join([*lines[: max_lines - 1], "..."])


def _tool_call_text(name: str, arguments: str) -> str:
    if arguments.strip():
        try:
            arguments = json.dumps(json.loads(arguments), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            pass
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
            return (_get_text(content), "user") if _get_text(content) else None
        case {"type": "message", "content": content}:
            return (_get_text(content), "answer") if _get_text(content) else None
        case {"type": "reasoning", "summary": summary}:
            return (_get_text(summary), "thinking") if _get_text(summary) else None
        case {"type": "function_call_output"}:
            return None
        case _:
            return None
