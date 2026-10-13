"""Translate Claude Code's stream-json into the events and history items FaltooBot renders.

Pure functions with no I/O: `translate` maps one stream-json line to Responses-style
events, and the item builders give Claude's tool calls the shape the existing
renderers, `/inspect` and hand-off exports already understand.
"""

import json
import time
from types import SimpleNamespace
from typing import Any, cast

from faltoobot.gpt_utils import MessageItem, StreamingReplyItem
from faltoobot.tools import MAX_SHELL_OUTPUT


def event(event_type: str, **fields: Any) -> StreamingReplyItem:
    return cast(StreamingReplyItem, SimpleNamespace(type=event_type, **fields))


def content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if isinstance(part.get("text"), str):
            parts.append(part["text"])
        elif part.get("type") in {"input_image", "image"}:
            parts.append("[image]")
    return "\n".join(parts)


def clip(text: str) -> str:
    if len(text) <= MAX_SHELL_OUTPUT:
        return text
    return f"{text[:MAX_SHELL_OUTPUT]}\n... [{len(text) - MAX_SHELL_OUTPUT} characters cut]"


def function_call_item(block: dict[str, Any]) -> MessageItem:
    name = str(block.get("name") or "")
    raw_input = block.get("input")
    arguments: dict[str, Any] = raw_input if isinstance(raw_input, dict) else {}
    if name == "Bash":
        # comment: the existing renderers special-case the harness shell tool.
        name = "run_shell_call"
        arguments = {
            "command": str(arguments.get("command") or ""),
            "command_summary": str(arguments.get("description") or ""),
        }
    return {
        "type": "function_call",
        "name": name,
        "call_id": str(block.get("id") or ""),
        "arguments": json.dumps(arguments, ensure_ascii=False),
    }


def output_fields(block: dict[str, Any]) -> dict[str, Any]:
    return {
        "call_id": str(block.get("tool_use_id") or ""),
        "output": clip(content_text(block.get("content"))),
        "status": "completed",
    }


def function_call_output_item(block: dict[str, Any]) -> MessageItem:
    return {"type": "function_call_output", **output_fields(block)}


def turn_usage(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    cached = int(raw.get("cache_read_input_tokens") or 0)
    input_tokens = (
        int(raw.get("input_tokens") or 0)
        + cached
        + int(raw.get("cache_creation_input_tokens") or 0)
    )
    output_tokens = int(raw.get("output_tokens") or 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "input_tokens_details": {"cached_tokens": cached},
    }


def rate_limits(info: Any) -> dict[str, dict[str, float]]:
    windows = info.get("unifiedWindows") if isinstance(info, dict) else None
    limits: dict[str, dict[str, float]] = {}
    for name, window in (windows or {}).items():
        if not isinstance(window, dict):
            continue
        used = window.get("utilization")
        if isinstance(used, int | float):
            limits[str(name)] = {
                "used_percent": round(float(used) * 100, 1),
                "reset_after_seconds": max(
                    0.0, float(window.get("resetsAt") or 0) - time.time()
                ),
            }
    return limits


def _block_delta(delta: dict[str, Any]) -> list[StreamingReplyItem]:
    if delta.get("type") == "text_delta":
        return [event("response.output_text.delta", delta=str(delta.get("text") or ""))]
    if delta.get("type") == "thinking_delta":
        return [
            event(
                "response.reasoning_summary_text.delta",
                delta=str(delta.get("thinking") or ""),
            )
        ]
    return []


def _block_stop(block: str | None) -> list[StreamingReplyItem]:
    if block == "text":
        return [event("response.output_text.done")]
    if block == "thinking":
        return [event("response.reasoning_summary_text.done")]
    return []


def translate(  # noqa: PLR0911
    payload: dict[str, Any], block: str | None
) -> tuple[list[StreamingReplyItem], str | None]:
    """Events for one stream-json line, plus the content block now open."""
    match payload:
        case {"type": "stream_event", "event": {"type": "content_block_start"} as ev}:
            started = str(
                cast(dict[str, Any], ev.get("content_block") or {}).get("type") or ""
            )
            added = event(
                "response.reasoning_summary_part.added", part=SimpleNamespace(text="")
            )
            return ([added] if started == "thinking" else []), started
        case {"type": "stream_event", "event": {"type": "content_block_delta"} as ev}:
            return _block_delta(cast(dict[str, Any], ev.get("delta") or {})), block
        case {"type": "stream_event", "event": {"type": "content_block_stop"}}:
            return _block_stop(block), None
        case {"type": "assistant", "message": {"content": list(blocks)}}:
            return [
                event("response.output_item.done", item=function_call_item(item))
                for item in blocks
                if isinstance(item, dict) and item.get("type") == "tool_use"
            ], block
        case {"type": "user", "message": {"content": list(blocks)}}:
            return [
                event("function_call_output", **output_fields(item))
                for item in blocks
                if isinstance(item, dict) and item.get("type") == "tool_result"
            ], block
        case {"type": "rate_limit_event"}:
            limits = rate_limits(payload.get("rate_limit_info"))
            return (
                [event("codex.rate_limits", rate_limits=limits)] if limits else []
            ), block
    return [], block
