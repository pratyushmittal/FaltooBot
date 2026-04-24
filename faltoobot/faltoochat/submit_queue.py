import json
from contextlib import contextmanager
from pathlib import Path
from threading import Lock, RLock
from typing import Any
from uuid import uuid4

from faltoobot import sessions
from faltoobot.gpt_utils import MessageHistory, MessageItem

SUBMIT_QUEUE_FILE = "submit-queue.json"
_QUEUE_LOCKS: dict[str, RLock] = {}
_QUEUE_LOCKS_GUARD = Lock()


def _queue_path(session: sessions.Session) -> Path:
    return session.chat_root / SUBMIT_QUEUE_FILE


def _queue_lock(session: sessions.Session) -> RLock:
    key = str(_queue_path(session))
    with _QUEUE_LOCKS_GUARD:
        if key not in _QUEUE_LOCKS:
            _QUEUE_LOCKS[key] = RLock()
        return _QUEUE_LOCKS[key]


@contextmanager
def _locked_queue(session: sessions.Session):
    lock = _queue_lock(session)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def _read_queue(path: Path) -> MessageHistory:
    # comment: the queue file won't exist until the first queued message is saved.
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # comment: older or broken payloads may contain invalid JSON. Treat them as empty.
        return []
    # comment: older or broken payloads may decode to the wrong shape. Treat them as empty.
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _write_queue(path: Path, queue: MessageHistory) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    temp.write_text(
        json.dumps(queue, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def _queue(session: sessions.Session) -> MessageHistory:
    return _read_queue(_queue_path(session))


def _queue_with_message_id(message: MessageItem) -> MessageItem:
    # comment: queue entries are only for upcoming user prompts.
    if message.get("type") != "message" or message.get("role") != "user":
        raise ValueError("Queue items must be user messages")
    queued = dict(message)
    # comment: callers may pass a fresh message without an id yet.
    if not isinstance(queued.get("id"), str) or not queued["id"]:
        queued["id"] = str(uuid4())
    return queued


def _message_index(queue: MessageHistory, message_id: str) -> int | None:
    for index, message in enumerate(queue):
        if message.get("id") == message_id:
            return index
    return None


def get_queue(session: sessions.Session) -> MessageHistory:
    with _locked_queue(session):
        return _queue(session)


def add_to_queue(session: sessions.Session, message: MessageItem) -> MessageHistory:
    with _locked_queue(session):
        queue = _queue(session)
        queue.append(_queue_with_message_id(message))
        _write_queue(_queue_path(session), queue)
        return queue


def move_up(session: sessions.Session, message_id: str) -> MessageHistory:
    with _locked_queue(session):
        queue = _queue(session)
        # comment: missing ids or the first item can't move any higher.
        if (index := _message_index(queue, message_id)) not in {None, 0}:
            queue[index - 1], queue[index] = queue[index], queue[index - 1]
            _write_queue(_queue_path(session), queue)
        return queue


def move_down(session: sessions.Session, message_id: str) -> MessageHistory:
    with _locked_queue(session):
        queue = _queue(session)
        index = _message_index(queue, message_id)
        # comment: missing ids or the last item can't move any lower.
        if index is not None and index < len(queue) - 1:
            queue[index + 1], queue[index] = queue[index], queue[index + 1]
            _write_queue(_queue_path(session), queue)
        return queue


def remove_from_queue(session: sessions.Session, message_id: str) -> MessageHistory:
    with _locked_queue(session):
        queue = [
            message for message in _queue(session) if message.get("id") != message_id
        ]
        _write_queue(_queue_path(session), queue)
        return queue


def set_auto_submit(session: sessions.Session, message_id: str) -> MessageHistory:
    with _locked_queue(session):
        queue = _queue(session)
        # comment: callers may reference a removed queue item. Ignore that safely.
        if (index := _message_index(queue, message_id)) is not None:
            queue[index] = {**queue[index], "auto_submit": True}
            _write_queue(_queue_path(session), queue)
        return queue


def remove_auto_submit(session: sessions.Session, message_id: str) -> MessageHistory:
    with _locked_queue(session):
        queue = _queue(session)
        # comment: callers may reference a removed queue item. Ignore that safely.
        if (index := _message_index(queue, message_id)) is not None:
            updated: dict[str, Any] = dict(queue[index])
            updated.pop("auto_submit", None)
            queue[index] = updated
            _write_queue(_queue_path(session), queue)
        return queue
