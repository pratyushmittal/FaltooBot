import json
from typing import Any

from faltoobot.sessions import MessagesJson

TOOL_INSPECT_LIMIT = 20
INSPECT_TEXT_LIMIT = 160


def _inspect_text(value: Any) -> str:
    text = " ".join(("" if value is None else str(value)).split())
    if len(text) <= INSPECT_TEXT_LIMIT:
        return text
    return f"{text[: INSPECT_TEXT_LIMIT - 1]}…"


def _tool_arguments(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _tool_summary(name: str, arguments: dict[str, Any]) -> str:
    for key in ("command_summary", "summary", "query", "image_path", "skill_name"):
        summary = _inspect_text(arguments.get(key))
        if summary:
            return summary
    return ""


def _tool_call_text(item: dict[str, Any]) -> str | None:
    item_type = item.get("type")
    if item_type == "function_call":
        name = _inspect_text(item.get("name")) or "function_call"
        summary = _tool_summary(name, _tool_arguments(item.get("arguments")))
        return f"{name}: {summary}" if summary else name
    if item_type == "web_search_call":
        action = item.get("action")
        query = _inspect_text(action.get("query")) if isinstance(action, dict) else ""
        return f"web_search: {query}" if query else "web_search"
    if isinstance(item_type, str) and item_type.endswith("_call"):
        return item_type.replace("_", " ")
    return None


def inspect_text_for_messages(messages_json: MessagesJson) -> str:
    calls = [
        text
        for item in messages_json["messages"]
        if isinstance(item, dict) and (text := _tool_call_text(item))
    ][-TOOL_INSPECT_LIMIT:]
    if not calls:
        return "No tool calls yet."
    lines = ["Recent tool calls:"]
    lines.extend(f"{index}. {text}" for index, text in enumerate(calls, start=1))
    return "\n".join(lines)
