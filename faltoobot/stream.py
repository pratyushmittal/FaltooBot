import json
from typing import Any

from faltoobot.gpt_utils import StreamingReplyItem

MAX_TOOL_LINES = 5


def _clip_lines(text: str, max_lines: int = MAX_TOOL_LINES) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join([*lines[: max_lines - 1], "..."])


def _safe_class_name(value: str) -> str:
    return value.replace(".", "-")


def _tool_text(item: Any) -> str:
    if hasattr(item, "to_dict"):
        item = item.to_dict()

    match item:
        case {"type": "function_call", "name": str(name), "arguments": str(arguments)}:
            if arguments.strip():
                try:
                    arguments = json.dumps(
                        json.loads(arguments),
                        ensure_ascii=False,
                        indent=2,
                    )
                except json.JSONDecodeError:
                    pass
                return _clip_lines(f"{name}\n{arguments}")
            return name
        case {"type": "web_search_call", "action": {"query": str(query)}}:
            return f"web search\n{query.strip()}" if query.strip() else "web search"
        case {"type": str(item_type)} if item_type.endswith("_call"):
            return item_type.replace("_", " ")
        case _:
            return ""


def get_event_text(event: StreamingReplyItem) -> tuple[bool, str, str]:
    event_type = event.type
    match event_type:
        case (
            "response.created"
            | "response.in_progress"
            | "response.completed"
            | "response.output_item.added"
            | "response.content_part.added"
            | "response.content_part.done"
            | "response.function_call_arguments.delta"
        ):
            is_new, classes, text = False, "", ""
        case (
            "function_call_output"
            | "response.function_call_arguments.done"
            | "response.output_text.done"
            | "response.reasoning_summary_part.done"
            | "response.reasoning_summary_text.done"
            | "response.reasoning_text.done"
            | "response.web_search_call.completed"
        ):
            is_new, classes, text = True, "", ""
        case "response.output_item.done":
            is_new, classes, text = (
                True,
                "tool",
                _tool_text(getattr(event, "item", None)),
            )
        case "response.reasoning_summary_part.added":
            is_new, classes, text = (
                True,
                "thinking",
                str(getattr(getattr(event, "part", None), "text", "") or ""),
            )
        case "response.reasoning_summary_text.delta" | "response.reasoning_text.delta":
            value = getattr(event, "delta", "")
            is_new, classes, text = (
                False,
                "thinking",
                value if isinstance(value, str) else "",
            )
        case "response.output_text.delta":
            value = getattr(event, "delta", "")
            is_new, classes, text = (
                False,
                "answer",
                value if isinstance(value, str) else "",
            )
        case "response.web_search_call.in_progress":
            is_new, classes, text = True, "tool", "Web search"
        case "response.web_search_call.searching":
            is_new, classes, text = False, "tool", "\nsearching"
        case _:
            is_new, classes, text = (
                True,
                f"{_safe_class_name(event_type)} unknown",
                f"Unknown type: {event_type}\n\n",
            )
    return is_new, classes, text
