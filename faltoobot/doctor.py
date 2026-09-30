import json
import os
from pathlib import Path
from typing import cast

from faltoobot.config import Config
from faltoobot.gpt_utils import MessageHistory
from faltoobot.sessions import (
    LAST_USED_FILE,
    MESSAGES_FILE,
    ensure_function_call_outputs,
)


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
