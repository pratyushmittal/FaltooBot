import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
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


class MessagesJson(TypedDict):
    id: str
    chat_key: str
    workspace: str
    system_prompt: str
    messages: MessageHistory
    message_ids: list[str]


Attachment = str | Path
Session: TypeAlias = tuple[str, str]


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


def _normalized_messages_json(
    chat_key: str,
    session_id: str,
    payload: dict[str, Any],
    workspace: Path | None = None,
) -> MessagesJson:
    if workspace is None:
        saved_workspace = payload.get("workspace")
        workspace = (
            Path(saved_workspace)
            if isinstance(saved_workspace, str)
            else get_messages_path((chat_key, session_id)).parent / WORKSPACE_DIR
        )
    else:
        workspace = workspace.expanduser()

    system_prompt = payload.get("system_prompt")
    return {
        "id": session_id,
        "chat_key": chat_key,
        "workspace": str(workspace),
        "system_prompt": system_prompt if isinstance(system_prompt, str) else "",
        "messages": [item for item in payload.get("messages", [])],
        "message_ids": [item for item in payload.get("message_ids", [])],
    }


def _write_text_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    temp.write_text(value, encoding="utf-8")
    temp.replace(path)


def get_last_used_session_id(chat_key: str) -> str | None:
    path = app_root() / "sessions" / _validate_chat_key(chat_key) / LAST_USED_FILE
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def _session_parts(session: Session) -> tuple[str, str]:
    chat_key, session_id = session
    return _validate_chat_key(chat_key), session_id


def get_messages_path(session: Session) -> Path:
    chat_key, session_id = _session_parts(session)
    return app_root() / "sessions" / chat_key / session_id / MESSAGES_FILE


def get_session(
    chat_key: str,
    session_id: str | None = None,
    workspace: Path | None = None,
) -> Session:
    chat_key = _validate_chat_key(chat_key)
    session_id = session_id or get_last_used_session_id(chat_key) or str(uuid4())
    session = (chat_key, session_id)
    messages_path = get_messages_path(session)
    # comment: load saved state, normalize it, then ensure the workspace and metadata files exist.
    payload = (
        json.loads(messages_path.read_text(encoding="utf-8"))
        if messages_path.exists()
        else {}
    )
    messages_json = _normalized_messages_json(
        chat_key,
        session_id,
        payload,
        workspace,
    )
    messages_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_path = Path(messages_json["workspace"])
    workspace_path.mkdir(parents=True, exist_ok=True)
    # comment: new workspaces should always have AGENTS.md so long-term notes have a stable home.
    (workspace_path / "AGENTS.md").touch(exist_ok=True)
    # comment: update the messages file on disk.
    _write_text_atomic(
        messages_path,
        json.dumps(messages_json, indent=2, ensure_ascii=False) + "\n",
    )
    # comment: update the last-used session marker on disk.
    _write_text_atomic(
        app_root() / "sessions" / chat_key / LAST_USED_FILE,
        f"{session_id}\n",
    )
    return session


def get_messages(session: Session) -> MessagesJson:
    chat_key, session_id = _session_parts(session)
    messages_path = get_messages_path(session)
    payload = (
        json.loads(messages_path.read_text(encoding="utf-8"))
        if messages_path.exists()
        else {}
    )
    if not payload:
        get_session(chat_key=chat_key, session_id=session_id)
        payload = (
            json.loads(messages_path.read_text(encoding="utf-8"))
            if messages_path.exists()
            else {}
        )
    return _normalized_messages_json(chat_key, session_id, payload)


def get_last_usage(session: Session) -> dict[str, Any] | None:
    for item in reversed(get_messages(session)["messages"]):
        if not isinstance(item, dict):
            continue
        usage = item.get("usage")
        if isinstance(usage, dict):
            return cast(dict[str, Any], usage)
    return None


def set_messages(session: Session, messages_json: MessagesJson) -> None:
    chat_key, session_id = _session_parts(session)
    get_session(
        chat_key=chat_key,
        session_id=session_id,
        workspace=Path(messages_json["workspace"]),
    )
    _write_text_atomic(
        get_messages_path(session),
        json.dumps(messages_json, indent=2, ensure_ascii=False) + "\n",
    )


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


async def append_user_turn(
    session: Session,
    *,
    question: str,
    attachments: Sequence[Attachment] | None = None,
    message_ids: Sequence[str] = (),
) -> bool:
    config = build_config()
    # comment: load the current session payload and skip fully-duplicate message ids.
    messages_json = get_messages(session)
    fresh_message_ids = [
        item for item in message_ids if item not in messages_json["message_ids"]
    ]
    if message_ids and not fresh_message_ids:
        return False

    # comment: convert the user turn into the exact content shape expected by the model.
    workspace = Path(messages_json["workspace"])
    text = question.strip()
    if attachments:
        content: str | list[dict[str, Any]] = []
        if text:
            content.append({"type": "input_text", "text": text})
        content.extend(await _upload_attachments(attachments, workspace, config))
        if not content:
            raise ValueError("Question or attachments required")
    else:
        if not text:
            raise ValueError("Question or attachments required")
        content = text

    # comment: append the normalized user turn and persist the updated session history.
    messages_json["messages"].append(
        {
            "type": "message",
            "role": "user",
            "content": content,
        }
    )
    messages_json["message_ids"].extend(fresh_message_ids)
    set_messages(session, messages_json)
    return True


async def _get_answer_streaming_from_history(
    session: Session,
) -> AsyncIterator[StreamingReplyItem]:
    config = build_config()
    messages_json = get_messages(session)
    workspace = Path(messages_json["workspace"])
    chat_key = session[0]
    tools: list[Tool] = [
        get_run_shell_call_tool(workspace),
        get_load_image_tool(workspace),
    ]
    available_skills, load_skill_tool = get_load_skill_tool(
        workspace,
        chat_key=chat_key,
    )
    if available_skills:
        tools.append(load_skill_tool)

    instructions = messages_json["system_prompt"]
    if not instructions:
        instructions = get_system_instructions(config, chat_key, workspace)
        messages_json["system_prompt"] = instructions
    set_messages(session, messages_json)

    async for event in get_streaming_reply(
        instructions=instructions,
        input=messages_json["messages"],
        tools=tools,
        prompt_cache_key=messages_json["id"],
    ):
        if event.type in {"function_call_output", "response.completed"}:
            set_messages(session, messages_json)
        yield event


async def get_answer_from_history(session: Session) -> str:
    answer = ""
    async for event in _get_answer_streaming_from_history(session):
        if event.type == "response.completed":
            answer = _assistant_text_from_completed_event(
                cast(ResponseCompletedEvent, event)
            )
    return answer


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
    stored = await append_user_turn(
        session,
        question=question,
        attachments=attachments,
        message_ids=[message_id] if message_id else [],
    )
    if message_id and not stored:
        return

    async for event in _get_answer_streaming_from_history(session):
        yield event
