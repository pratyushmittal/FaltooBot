"""Read Claude Code's own transcript for a workspace as FaltooBot history items.

Claude keeps the conversation under ~/.claude/projects/<folder>/<id>.jsonl. This module
finds the file for a conversation id and converts it for history views, `/inspect`
and hand-off exports, dropping Claude Code's bookkeeping records on the way.
"""

import json
import os
import re
from pathlib import Path
from typing import Any, cast

from faltoobot.claude_stream import (
    content_text,
    function_call_item,
    function_call_output_item,
)
from faltoobot.gpt_utils import MessageHistory, MessageItem
from faltoobot.notify_queue import Notification, format_notification_message

BOOKKEEPING_TEXT = ("[Request interrupted",)
COMMAND_TAGS = ("<command-name>", "<local-command-stdout>", "<local-command-caveat>")
SYSTEM_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
TASK_SUMMARY = re.compile(r"<summary>(.*?)</summary>", re.S)

_transcript_cache: dict[Path, tuple[tuple[Path, int, int], MessageHistory]] = {}


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def transcript_path(session_id: str) -> Path | None:
    """Claude's transcript for a conversation id, wherever its project folder is."""
    if not session_id:
        return None
    return next((claude_home() / "projects").glob(f"*/{session_id}.jsonl"), None)


def _blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def _user_item(block: dict[str, Any]) -> MessageItem | None:
    if block.get("type") == "tool_result":
        return function_call_output_item(block)
    text = block.get("text")
    if not isinstance(text, str):
        return None
    if text.startswith(BOOKKEEPING_TEXT) or any(tag in text for tag in COMMAND_TAGS):
        return None
    # comment: harness notes we sent are shown elsewhere; keep only what the user wrote.
    text = SYSTEM_REMINDER.sub("", text).strip()
    return {"type": "message", "role": "user", "content": text} if text else None


def _task_notification(line: dict[str, Any]) -> MessageItem:
    text = content_text(line.get("message", {}).get("content"))
    summary = TASK_SUMMARY.search(text)
    body = summary.group(1).strip() if summary else "A background task finished."
    notification = cast(Notification, {"source": "Claude Code", "message": body})
    content = format_notification_message(notification)
    return {"type": "message", "role": "user", "content": content}


def _assistant_item(block: dict[str, Any]) -> MessageItem | None:
    match block:
        case {"type": "text", "text": str(text)} if text.strip():
            return {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        case {"type": "thinking", "thinking": str(thinking)} if thinking:
            return {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": thinking}],
            }
        case {"type": "tool_use"}:
            return function_call_item(block)
    return None


def _transcript_items(line: dict[str, Any]) -> list[MessageItem]:
    message = line.get("message")
    if not isinstance(message, dict) or line.get("isSidechain"):
        return []
    # comment: skill bodies and compaction summaries are Claude Code bookkeeping.
    if line.get("isMeta") or line.get("isCompactSummary"):
        return []
    if (line.get("origin") or {}).get("kind") == "task-notification":
        item = _task_notification(line)
        item["timestamp"] = str(line.get("timestamp") or "")
        return [item]
    convert = {"user": _user_item, "assistant": _assistant_item}.get(
        str(line.get("type"))
    )
    if convert is None:
        return []
    items = [
        item for block in _blocks(message.get("content")) if (item := convert(block))
    ]
    for item in items:
        item["timestamp"] = str(line.get("timestamp") or "")
    return items


def read_transcript(session_id: str) -> MessageHistory:
    """Convert Claude's own JSONL for a conversation into renderable history items."""
    path = transcript_path(session_id)
    if path is None:
        return []
    stat = path.stat()
    key = (path, stat.st_mtime_ns, stat.st_size)
    if (cached := _transcript_cache.get(path)) and cached[0] == key:
        return list(cached[1])
    items: MessageHistory = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            line = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(line, dict):
            items.extend(_transcript_items(line))
    # comment: history loads and /inspect re-read the same file; parse it once per change.
    _transcript_cache[path] = (key, items)
    return list(items)
