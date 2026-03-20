import asyncio
import json
import mimetypes
from collections.abc import AsyncGenerator
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from threading import Lock, RLock
from typing import Any, TypedDict
from uuid import uuid4

from openai import AsyncOpenAI
from PIL import Image

from faltoobot.config import app_root, build_config
from faltoobot.gpt_utils import get_streaming_reply
from faltoobot.tools import get_run_shell_call_tool

MESSAGES_FILE = "messages.json"
WORKSPACE_DIR = "workspace"
MAX_IMAGE_WIDTH = 1600
MAX_IMAGE_HEIGHT = 1200
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})
_SESSION_LOCKS: dict[str, RLock] = {}
_SESSION_LOCKS_GUARD = Lock()


class MessagesJson(TypedDict):
    id: str
    kind: str
    workspace: str
    messages: list[dict[str, Any]]
    message_ids: list[str]


Attachment = str | Path


def _sessions_dir() -> Path:
    return app_root() / "sessions"


def _session_root(session_id: str) -> Path:
    return _sessions_dir() / session_id


def _messages_path(session_id: str) -> Path:
    return _session_root(session_id) / MESSAGES_FILE


def _workspace_path(session_id: str, workspace: Path | None) -> Path:
    return (
        workspace.expanduser()
        if workspace
        else _session_root(session_id) / WORKSPACE_DIR
    )


def _basic_messages_json(
    session_id: str,
    *,
    kind: str,
    workspace: Path,
    current: dict[str, Any] | None = None,
) -> MessagesJson:
    return {
        "id": session_id,
        "kind": kind,
        "workspace": str(workspace),
        "messages": [
            item
            for item in (current or {}).get("messages", [])
            if isinstance(item, dict)
        ],
        "message_ids": [
            item
            for item in (current or {}).get("message_ids", [])
            if isinstance(item, str)
        ],
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, payload: dict[str, Any] | MessagesJson) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    temp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def _session_lock(session_id: str) -> RLock:
    with _SESSION_LOCKS_GUARD:
        if session_id not in _SESSION_LOCKS:
            _SESSION_LOCKS[session_id] = RLock()
        return _SESSION_LOCKS[session_id]


@contextmanager
def _locked_session(session_id: str):
    lock = _session_lock(session_id)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


class _AsyncSessionLock:
    def __init__(self, session_id: str) -> None:
        self._lock = _session_lock(session_id)

    async def __aenter__(self) -> None:
        while not self._lock.acquire(blocking=False):
            await asyncio.sleep(0.01)

    async def __aexit__(self, exc_type: object, exc: object, exc_tb: object) -> None:
        self._lock.release()


def get_session_id(
    kind: str = "whatsapp",
    session_id: str | None = None,
    workspace: Path | None = None,
) -> str:
    session_id = session_id or str(uuid4())
    root = _session_root(session_id)
    path = root / MESSAGES_FILE
    target_workspace = _workspace_path(session_id, workspace)

    with _locked_session(session_id):
        payload = _read_json(path)
        saved_workspace = payload.get("workspace")
        messages_json = _basic_messages_json(
            session_id,
            kind=kind,
            workspace=Path(saved_workspace)
            if isinstance(saved_workspace, str) and workspace is None
            else target_workspace,
            current=payload,
        )
        if workspace is not None:
            messages_json["workspace"] = str(target_workspace)
        if payload.get("kind") != kind:
            messages_json["kind"] = kind
        root.mkdir(parents=True, exist_ok=True)
        Path(messages_json["workspace"]).mkdir(parents=True, exist_ok=True)
        _write_json_atomic(path, messages_json)
    return session_id


def _coerce_messages_json(session_id: str, payload: dict[str, Any]) -> MessagesJson:
    workspace = payload.get("workspace")
    return _basic_messages_json(
        session_id,
        kind=str(payload.get("kind") or "whatsapp"),
        workspace=Path(workspace)
        if isinstance(workspace, str)
        else _workspace_path(session_id, None),
        current=payload,
    )


def get_messages(session_id: str) -> MessagesJson:
    with _locked_session(session_id):
        path = _messages_path(session_id)
        payload = _read_json(path)
        if not payload:
            get_session_id(session_id=session_id)
            payload = _read_json(path)
        return _coerce_messages_json(session_id, payload)


def set_messages(session_id: str, messages_json: MessagesJson) -> None:
    get_session_id(
        kind=messages_json.get("kind", "whatsapp"),
        session_id=session_id,
        workspace=Path(messages_json["workspace"]),
    )
    with _locked_session(session_id):
        _write_json_atomic(_messages_path(session_id), messages_json)


def _attachment_path(source: Attachment, workspace: Path) -> Path:
    path = (
        source if isinstance(source, Path) else Path(str(source).strip()).expanduser()
    )
    return path if path.is_absolute() else workspace / path


def _is_image_path(path: Path) -> bool:
    mime_type, _ = mimetypes.guess_type(path.name)
    return path.is_file() and (
        (mime_type or "").startswith("image/")
        or path.suffix.lower() in _IMAGE_EXTENSIONS
    )


def _fitted_image_size(width: int, height: int) -> tuple[int, int]:
    scale = min(MAX_IMAGE_WIDTH / width, MAX_IMAGE_HEIGHT / height, 1)
    return max(1, int(width * scale)), max(1, int(height * scale))


def _resized_image_upload(path: Path) -> BytesIO | None:
    with Image.open(path) as image:
        width, height = image.size
        target = _fitted_image_size(width, height)
        if target == (width, height):
            return None
        resized = image.resize(target, Image.Resampling.LANCZOS)
        buffer = BytesIO()
        format_name = "JPEG" if image.format in {"JPEG", "JPG"} else "PNG"
        suffix = ".jpg" if format_name == "JPEG" else ".png"
        resized.save(buffer, format=format_name)
    buffer.seek(0)
    buffer.name = f"{path.stem}-{target[0]}x{target[1]}{suffix}"
    return buffer


async def _upload_attachment(
    client: AsyncOpenAI, workspace: Path, source: Attachment
) -> dict[str, Any]:
    path = _attachment_path(source, workspace)
    if not path.exists():
        raise ValueError(f"Attachment not found: {source}")
    if not _is_image_path(path):
        raise ValueError(f"Unsupported attachment: {source}")
    if upload := _resized_image_upload(path):
        uploaded = await client.files.create(file=upload, purpose="vision")
    else:
        with path.open("rb") as handle:
            uploaded = await client.files.create(file=handle, purpose="vision")
    return {"type": "input_image", "file_id": uploaded.id, "detail": "auto"}


async def _upload_attachments(
    attachments: list[Attachment],
    workspace: Path,
    api_key: str,
) -> list[dict[str, Any]]:
    client = AsyncOpenAI(api_key=api_key)
    try:
        return [
            await _upload_attachment(client, workspace, source)
            for source in attachments
        ]
    finally:
        await client.close()


def _response_output(value: Any) -> list[dict[str, Any]]:
    output = getattr(value, "output", None)
    if not isinstance(output, list):
        return []
    items: list[dict[str, Any]] = []
    for item in output:
        if hasattr(item, "to_dict"):
            raw = item.to_dict()
        else:
            raw = item
        if isinstance(raw, dict):
            items.append(raw)
    return items


async def get_answer(
    session_id: str,
    question: str,
    attachments: list[Attachment] | None = None,
    message_id: str | None = None,
) -> MessagesJson:
    async for _ in get_answer_streaming(
        session_id=session_id,
        question=question,
        attachments=attachments,
        message_id=message_id,
    ):
        pass
    return get_messages(session_id)


async def get_answer_streaming(
    session_id: str,
    question: str,
    attachments: list[Attachment] | None = None,
    message_id: str | None = None,
) -> AsyncGenerator[Any, None]:
    config = build_config()
    get_session_id(session_id=session_id)

    async with _AsyncSessionLock(session_id):
        messages_json = get_messages(session_id)
        if message_id and message_id in messages_json["message_ids"]:
            return

        workspace = Path(messages_json["workspace"])
        text = question.strip()
        if attachments:
            content: str | list[dict[str, Any]] = []
            if text:
                content.append({"type": "input_text", "text": text})
            content.extend(
                await _upload_attachments(attachments, workspace, config.openai_api_key)
            )
        else:
            content = text
        if not content:
            raise ValueError("Question or attachments required")

        user_message = {"type": "message", "role": "user", "content": content}
        messages_json["messages"].append(user_message)
        if message_id:
            messages_json["message_ids"].append(message_id)
        set_messages(session_id, messages_json)

        async for event in get_streaming_reply(
            model=config.openai_model,
            input=list(messages_json["messages"]),
            tools=[get_run_shell_call_tool(Path(messages_json["workspace"]))],
        ):
            response_output = _response_output(event)
            if response_output:
                messages_json["messages"].extend(response_output)
                set_messages(session_id, messages_json)
            yield event
