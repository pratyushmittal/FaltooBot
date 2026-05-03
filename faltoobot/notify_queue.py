import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import NotRequired, TextIO, TypedDict
from uuid import uuid4

from faltoobot.config import app_root


class Notification(TypedDict):
    id: str
    chat_key: str
    message: str
    created_at: str
    source: NotRequired[str]


ClaimedNotification = tuple[Path, Notification]


def _queue_root() -> Path:
    return app_root() / "notify-queue"


def _pending_dir() -> Path:
    return _queue_root() / "pending"


def _processing_dir() -> Path:
    return _queue_root() / "processing"


def _read_notification(path: Path) -> Notification | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    notification_id = payload.get("id")
    chat_key = payload.get("chat_key")
    message = payload.get("message")
    created_at = payload.get("created_at")
    source = payload.get("source")
    if not all(
        isinstance(value, str) and value
        for value in (notification_id, chat_key, message, created_at)
    ):
        return None
    if source is not None and not isinstance(source, str):
        return None
    notification: Notification = {
        "id": notification_id,
        "chat_key": chat_key,
        "message": message,
        "created_at": created_at,
    }
    if source is not None:
        notification["source"] = source
    return notification


def parse_message(message: str | None, stdin: TextIO) -> str:
    if message:
        return message
    # comment: interactive shells have no piped stdin, so fail fast instead of waiting forever.
    if stdin.isatty():
        raise SystemExit("notify requires a message argument or stdin")
    parsed = stdin.read().strip()
    if not parsed:
        raise SystemExit("notify requires a non-empty message")
    return parsed


def enqueue_notification(
    chat_key: str, message: str, *, source: str | None = None
) -> str:
    notification_id = f"notify_{uuid4().hex}"
    notification: Notification = {
        "id": notification_id,
        "chat_key": chat_key,
        "message": message,
        "created_at": datetime.now(UTC).isoformat(),
    }
    if source:
        notification["source"] = source
    pending = _pending_dir()
    pending.mkdir(parents=True, exist_ok=True)
    path = pending / f"{notification_id}.json"
    temp = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    temp.write_text(json.dumps(notification, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
    return notification_id


def claim_notifications(
    matches: Callable[[Notification], bool],
) -> list[ClaimedNotification]:
    pending = _pending_dir()
    processing = _processing_dir()
    if not pending.is_dir():
        return []
    processing.mkdir(parents=True, exist_ok=True)
    claimed: list[ClaimedNotification] = []
    for path in sorted(pending.glob("*.json")):
        notification = _read_notification(path)
        if notification is None or not matches(notification):
            continue
        claimed_path = processing / path.name
        try:
            path.replace(claimed_path)
        except FileNotFoundError:
            continue
        claimed_notification = _read_notification(claimed_path)
        if claimed_notification is None:
            claimed_path.unlink(missing_ok=True)
            continue
        claimed.append((claimed_path, claimed_notification))
    return claimed


def ack_notification(path: Path) -> None:
    """Delete a claimed notification after it has been handled successfully."""
    path.unlink(missing_ok=True)


def requeue_notification(path: Path) -> None:
    pending = _pending_dir()
    pending.mkdir(parents=True, exist_ok=True)
    path.replace(pending / path.name)


def format_notification_message(notification: Notification) -> str:
    lines = ["# Background update", ""]
    source = notification.get("source")
    if source:
        lines.extend([f"source: {source}", ""])
    lines.extend(["## message", notification["message"]])
    return "\n".join(lines).strip()
