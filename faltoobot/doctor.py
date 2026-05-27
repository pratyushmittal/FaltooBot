import json
import os
from pathlib import Path
from typing import Any, TypeAlias, cast

from faltoobot.config import Config
from faltoobot.sessions import LAST_USED_FILE, MESSAGES_FILE

MessageItem: TypeAlias = dict[str, Any]
MessageHistory: TypeAlias = list[MessageItem]

MISSING_FUNCTION_CALL_OUTPUT = "Tool call failed before output was saved."


def _call_id(item: MessageItem, item_type: str) -> str | None:
    if item.get("type") != item_type:
        return None
    call_id = item.get("call_id")
    return call_id if isinstance(call_id, str) and call_id else None


def _missing_function_call_output(call_id: str) -> MessageItem:
    return {
        "id": f"fco_{call_id}",
        "type": "function_call_output",
        "call_id": call_id,
        "output": MISSING_FUNCTION_CALL_OUTPUT,
        "status": "completed",
    }


def ensure_function_call_outputs(items: MessageHistory) -> bool:
    """Mutate history so every function_call has a non-null output item."""
    output_ids = {
        call_id
        for item in items
        if (call_id := _call_id(item, "function_call_output"))
        and item.get("output") is not None
    }
    fixed: MessageHistory = []
    pending: list[str] = []
    changed = False

    for item in items:
        output_call_id = _call_id(item, "function_call_output")
        if output_call_id:
            item = dict(item)
            if item.get("output") is None:
                # comment: Responses treats null output as not answering the call.
                item.update(output=MISSING_FUNCTION_CALL_OUTPUT, status="completed")
                changed = True
            if output_call_id in pending:
                pending.remove(output_call_id)
            fixed.append(item)
            continue

        call_id = _call_id(item, "function_call")
        if call_id and call_id not in output_ids and call_id not in pending:
            pending.append(call_id)
        elif not call_id and pending:
            fixed.extend(_missing_function_call_output(call_id) for call_id in pending)
            pending.clear()
            changed = True
        fixed.append(item)

    if pending:
        fixed.extend(_missing_function_call_output(call_id) for call_id in pending)
        changed = True
    if changed:
        items[:] = fixed
    return changed


def _last_used_available(chat_root: Path) -> bool:
    path = chat_root / LAST_USED_FILE
    if not path.exists():
        return False
    try:
        session_id = path.read_text(encoding="utf-8").strip()
    except OSError:
        # comment: unreadable marker should be rebuilt from current session mtimes.
        return False
    if not session_id or session_id in {".", ".."} or "/" in session_id:
        # comment: corrupt marker should not be trusted.
        return False
    return (chat_root / session_id / MESSAGES_FILE).exists()


def _latest_session_id(chat_root: Path) -> str | None:
    message_paths = list(chat_root.glob(f"*/{MESSAGES_FILE}"))
    if not message_paths:
        return None
    message_paths.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return message_paths[0].parent.name


def heal_last_used_files(config: Config) -> bool:
    sessions_dir = config.sessions_dir
    if not sessions_dir.exists():
        # comment: fresh installs do not have session roots to heal.
        return False

    changed = False
    for chat_root in sessions_dir.iterdir():
        if not chat_root.is_dir() or _last_used_available(chat_root):
            continue
        session_id = _latest_session_id(chat_root)
        if session_id is None:
            # comment: chats without any messages.json have no usable session.
            continue
        (chat_root / LAST_USED_FILE).write_text(f"{session_id}\n", encoding="utf-8")
        changed = True
    return changed


def heal_function_call_outputs(config: Config) -> bool:
    sessions_dir = config.sessions_dir
    if not sessions_dir.exists():
        # comment: fresh installs do not have histories to heal.
        return False

    changed = False
    for path in sessions_dir.rglob("messages.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # comment: leave unreadable/corrupt history files untouched during doctor runs.
            continue
        messages = data.get("messages") if isinstance(data, dict) else None
        if not isinstance(messages, list):
            # comment: skip old/corrupt session files that are not normal histories.
            continue
        if not ensure_function_call_outputs(cast(MessageHistory, messages)):
            continue

        stat = path.stat()
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        changed = True
    return changed


def main(config: Config) -> list[str]:
    changes: list[str] = []
    if heal_last_used_files(config):
        changes.append("doctor:heal-last-used")
    if heal_function_call_outputs(config):
        changes.append("doctor:heal-function-call-outputs")
    return changes
