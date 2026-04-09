import asyncio
import hashlib
import json
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path
from threading import Lock, RLock
from typing import Any, AsyncIterator, TypeAlias, TypedDict, cast
from uuid import uuid4

from openai import AsyncOpenAI
from openai.types.responses import (
    ResponseCompletedEvent,
    ResponseOutputMessage,
    ResponseOutputText,
)

from faltoobot.config import Config, app_root, build_config
from faltoobot.gpt_utils import (
    MessageHistory,
    MessageItem,
    StreamingReplyItem,
    Tool,
    get_streaming_reply,
)
from faltoobot.images import inline_image_item, upload_attachment
from faltoobot.instructions import get_system_instructions
from faltoobot.openai_auth import uses_chatgpt_oauth
from faltoobot.skills import get_load_skill_tool
from faltoobot.tools import get_load_image_tool, get_run_shell_call_tool

MESSAGES_FILE = "messages.json"
LAST_USED_FILE = "last_used"
WORKSPACE_DIR = "workspace"
_SESSION_LOCKS: dict[str, RLock] = {}
_SESSION_LOCKS_GUARD = Lock()
_LAST_USED_LOCK = Lock()


class MessagesJson(TypedDict):
    id: str
    chat_key: str
    workspace: str
    messages: MessageHistory
    message_ids: list[str]


Attachment = str | Path
Session: TypeAlias = tuple[str, str]


def _prompt_cache_key(messages_json: MessagesJson) -> str:
    return messages_json["id"]


def _sessions_dir() -> Path:
    return app_root() / "sessions"


def _validate_chat_key(chat_key: str) -> str:
    if not chat_key or chat_key in {".", ".."} or "/" in chat_key:
        raise ValueError(f"Invalid chat key: {chat_key!r}")
    return chat_key


def get_dir_chat_key(workspace: Path, *, is_sub_agent: bool = False) -> str:
    resolved = workspace.resolve()
    name = resolved.name or "root"
    digest = hashlib.md5(str(resolved).encode("utf-8")).hexdigest()[-6:]
    prefix = "sub-agent" if is_sub_agent else "code"
    return f"{prefix}@{name}-{digest}"


def _chat_root(chat_key: str) -> Path:
    return _sessions_dir() / _validate_chat_key(chat_key)


def _session_root(chat_key: str, session_id: str) -> Path:
    return _chat_root(chat_key) / session_id


def _last_used_path(chat_key: str) -> Path:
    return _chat_root(chat_key) / LAST_USED_FILE


def _messages_path(chat_key: str, session_id: str) -> Path:
    return _session_root(chat_key, session_id) / MESSAGES_FILE


def _workspace_path(chat_key: str, session_id: str, workspace: Path | None) -> Path:
    return (
        workspace.expanduser()
        if workspace
        else _session_root(chat_key, session_id) / WORKSPACE_DIR
    )


def _basic_messages_json(
    session_id: str,
    *,
    chat_key: str,
    workspace: Path,
    current: dict[str, Any],
) -> MessagesJson:
    return {
        "id": session_id,
        "chat_key": chat_key,
        "workspace": str(workspace),
        "messages": [item for item in current.get("messages", [])],
        "message_ids": [item for item in current.get("message_ids", [])],
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


def _read_last_used(chat_key: str) -> str | None:
    path = _last_used_path(chat_key)
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def get_last_used_session_id(chat_key: str) -> str | None:
    return _read_last_used(_validate_chat_key(chat_key))


def _session_parts(session: Session) -> tuple[str, str]:
    chat_key, session_id = session
    return _validate_chat_key(chat_key), session_id


def get_messages_path(session: Session) -> Path:
    chat_key, session_id = _session_parts(session)
    return _messages_path(chat_key, session_id)


def _write_text_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    temp.write_text(f"{value}\n", encoding="utf-8")
    temp.replace(path)


def _set_last_used(chat_key: str, session_id: str) -> None:
    with _LAST_USED_LOCK:
        _write_text_atomic(_last_used_path(chat_key), session_id)


def _session_lock(chat_key: str, session_id: str) -> RLock:
    key = str(_session_root(chat_key, session_id))
    with _SESSION_LOCKS_GUARD:
        if key not in _SESSION_LOCKS:
            _SESSION_LOCKS[key] = RLock()
        return _SESSION_LOCKS[key]


@contextmanager
def _locked_session(session: Session):
    chat_key, session_id = _session_parts(session)
    lock = _session_lock(chat_key, session_id)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


class _AsyncSessionLock:
    def __init__(self, session: Session) -> None:
        chat_key, session_id = _session_parts(session)
        self._lock = _session_lock(chat_key, session_id)

    async def __aenter__(self) -> None:
        while not self._lock.acquire(blocking=False):
            await asyncio.sleep(0.01)

    async def __aexit__(self, exc_type: object, exc: object, exc_tb: object) -> None:
        self._lock.release()


def get_session(
    chat_key: str,
    session_id: str | None = None,
    workspace: Path | None = None,
) -> Session:
    chat_key = _validate_chat_key(chat_key)
    session_id = session_id or _read_last_used(chat_key) or str(uuid4())
    session = (chat_key, session_id)
    root = _session_root(chat_key, session_id)
    path = _messages_path(chat_key, session_id)
    target_workspace = _workspace_path(chat_key, session_id, workspace)

    with _locked_session(session):
        payload = _read_json(path)
        saved_workspace = payload.get("workspace")
        session_workspace = (
            Path(saved_workspace)
            if isinstance(saved_workspace, str) and workspace is None
            else target_workspace
        )
        messages_json = _basic_messages_json(
            session_id,
            chat_key=chat_key,
            workspace=session_workspace,
            current=payload,
        )
        if workspace is not None:
            messages_json["workspace"] = str(target_workspace)
        root.mkdir(parents=True, exist_ok=True)
        workspace_path = Path(messages_json["workspace"])
        workspace_path.mkdir(parents=True, exist_ok=True)
        # comment: new workspaces should always have AGENTS.md so long-term notes have a stable home.
        (workspace_path / "AGENTS.md").touch(exist_ok=True)
        _write_json_atomic(path, messages_json)
        _set_last_used(chat_key, session_id)
    return session


def _coerce_messages_json(
    chat_key: str, session_id: str, payload: dict[str, Any]
) -> MessagesJson:
    workspace = payload.get("workspace")
    session_workspace = (
        Path(workspace)
        if isinstance(workspace, str)
        else _workspace_path(chat_key, session_id, None)
    )
    return _basic_messages_json(
        session_id,
        chat_key=chat_key,
        workspace=session_workspace,
        current=payload,
    )


def get_messages(session: Session) -> MessagesJson:
    chat_key, session_id = _session_parts(session)
    with _locked_session(session):
        path = _messages_path(chat_key, session_id)
        payload = _read_json(path)
        if not payload:
            get_session(chat_key=chat_key, session_id=session_id)
            payload = _read_json(path)
        return _coerce_messages_json(chat_key, session_id, payload)


def set_messages(session: Session, messages_json: MessagesJson) -> None:
    chat_key, session_id = _session_parts(session)
    get_session(
        chat_key=chat_key,
        session_id=session_id,
        workspace=Path(messages_json["workspace"]),
    )
    with _locked_session(session):
        _write_json_atomic(_messages_path(chat_key, session_id), messages_json)


async def _upload_attachments(
    attachments: Sequence[Attachment],
    workspace: Path,
    config: Config,
) -> list[dict[str, Any]]:
    if uses_chatgpt_oauth(config):
        # comment: ChatGPT Codex OAuth requests go straight to chatgpt.com responses, so
        # platform file uploads are unavailable. Inline images keep attachments working there.
        return [
            inline_image_item(workspace, source).to_dict() for source in attachments
        ]

    client = AsyncOpenAI(api_key=config.openai_api_key)
    try:
        return [
            (await upload_attachment(client, workspace, source)).to_dict()
            for source in attachments
        ]
    finally:
        await client.close()


def _assistant_text_from_completed_event(event: ResponseCompletedEvent) -> str:
    response = getattr(event, "response", None)
    if response is None:
        return ""
    output_text = getattr(response, "output_text", "")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    output = cast(
        list[object],
        getattr(response, "output", None) or getattr(response, "codex_output", []),
    )
    for item in reversed(output):
        if not isinstance(item, ResponseOutputMessage):
            continue
        text = "".join(
            part.text for part in item.content if isinstance(part, ResponseOutputText)
        ).strip()
        if text:
            return text
    return ""


async def get_answer(
    session: Session,
    question: str,
    attachments: Sequence[Attachment] | None = None,
    message_id: str | None = None,
) -> str:
    answer = ""
    async for event in get_answer_streaming(
        session=session,
        question=question,
        attachments=attachments,
        message_id=message_id,
    ):
        if event.type == "response.completed":
            answer = _assistant_text_from_completed_event(
                cast(ResponseCompletedEvent, event)
            )
    return answer


async def get_answer_streaming(
    session: Session,
    question: str,
    attachments: Sequence[Attachment] | None = None,
    message_id: str | None = None,
) -> AsyncIterator[StreamingReplyItem]:
    config = build_config()

    async with _AsyncSessionLock(session):
        messages_json = get_messages(session)
        if message_id and message_id in messages_json["message_ids"]:
            return

        workspace = Path(messages_json["workspace"])
        text = question.strip()
        if attachments:
            content: str | list[dict[str, Any]] = []
            if text:
                content.append({"type": "input_text", "text": text})
            content.extend(await _upload_attachments(attachments, workspace, config))
        else:
            content = text
        if not content:
            raise ValueError("Question or attachments required")

        user_message: MessageItem = {
            "type": "message",
            "role": "user",
            "content": content,
        }
        messages_json["messages"].append(user_message)
        if message_id:
            messages_json["message_ids"].append(message_id)
        set_messages(session, messages_json)

        tools: list[Tool] = [
            get_run_shell_call_tool(Path(messages_json["workspace"])),
            get_load_image_tool(Path(messages_json["workspace"])),
        ]
        available_skills, load_skill_tool = get_load_skill_tool(
            Path(messages_json["workspace"]),
            chat_key=session[0],
        )
        if available_skills:
            # comment: only expose the skill-loading tool when there is at least one local skill to load.
            tools.append(load_skill_tool)

        async for event in get_streaming_reply(
            instructions=get_system_instructions(config, session[0], workspace),
            input=messages_json["messages"],
            tools=tools,
            prompt_cache_key=_prompt_cache_key(messages_json),
        ):
            if event.type in {"function_call_output", "response.completed"}:
                set_messages(session, messages_json)
            yield event
