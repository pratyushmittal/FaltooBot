import base64
import hashlib
import json
import logging
from collections.abc import Sequence
from types import SimpleNamespace
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, TypedDict, cast
from uuid import uuid4

from openai import AsyncOpenAI, omit
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseOutputItem,
    ResponseOutputMessage,
    ResponseOutputText,
)
from openai.types.responses.response_output_item import ImageGenerationCall

from faltoobot.config import Config, app_root, build_config
from faltoobot.gpt_utils import (
    DISPLAY_ONLY_CONTENT_KEY,
    MessageHistory,
    STANDALONE_COMPACTION_KEY,
    StreamingReplyItem,
    Tool,
    get_openai_client,
    get_streaming_reply,
    trim_input,
)
from faltoobot.images import inline_image_item, upload_attachment
from faltoobot.instructions import get_system_instructions
from faltoobot.openai_auth import uses_chatgpt_oauth
from faltoobot.skills import get_load_skill_tool
from faltoobot.tools import get_load_image_tool, get_run_shell_call_tool
from faltoobot.websockets import prewarm as websocket_prewarm
from faltoobot.websockets import streaming_reply as websocket_streaming_reply

MESSAGES_FILE = "messages.json"
LAST_USED_FILE = "last_used"
WORKSPACE_DIR = "workspace"
GENERATED_IMAGES_DIR = ".generated-images"

logger = logging.getLogger("faltoobot")


class MessagesJson(TypedDict):
    id: str
    chat_key: str
    workspace: str
    system_prompt: str
    messages: MessageHistory
    message_ids: list[str]


Attachment = str | Path


@dataclass
class Session:
    chat_key: str
    session_id: str

    @property
    def chat_root(self) -> Path:
        return _chat_root(self.chat_key)

    @property
    def session_dir(self) -> Path:
        return self.chat_root / _validate_session_id(self.session_id)

    @property
    def messages_path(self) -> Path:
        return self.session_dir / MESSAGES_FILE


def _validate_chat_key(chat_key: str) -> str:
    if not chat_key or chat_key in {".", ".."} or "/" in chat_key:
        raise ValueError(f"Invalid chat key: {chat_key!r}")
    return chat_key


def _validate_session_id(session_id: str) -> str:
    if not session_id or session_id in {".", ".."} or "/" in session_id:
        raise ValueError(f"Invalid session id: {session_id!r}")
    return session_id


def _chat_root(chat_key: str) -> Path:
    return app_root() / "sessions" / _validate_chat_key(chat_key)


def get_dir_chat_key(workspace: Path, *, is_sub_agent: bool = False) -> str:
    resolved = workspace.resolve()
    name = resolved.name or "root"
    digest = hashlib.md5(str(resolved).encode("utf-8")).hexdigest()[-6:]
    prefix = "sub-agent" if is_sub_agent else "code"
    return f"{prefix}@{name}-{digest}"


def _normalized_messages_json(
    chat_key: str,
    session_id: str,
    session_dir: Path,
    payload: dict[str, Any],
    workspace: Path | None = None,
) -> MessagesJson:
    if workspace is None:
        saved_workspace = payload.get("workspace")
        workspace = (
            Path(saved_workspace)
            if isinstance(saved_workspace, str)
            else session_dir / WORKSPACE_DIR
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


def _get_last_used_session_id(chat_key: str) -> str | None:
    chat_root = _chat_root(chat_key)
    try:
        session_id = _validate_session_id(
            (chat_root / LAST_USED_FILE).read_text(encoding="utf-8").strip()
        )
    except (OSError, ValueError):
        session_id = None

    if session_id and (chat_root / session_id / MESSAGES_FILE).exists():
        return session_id

    for path in chat_root.glob(f"*/{MESSAGES_FILE}"):
        logger.warning("Missing last_used for %s; using %s", chat_key, path.parent.name)
        return path.parent.name
    return None


def set_last_used(session: Session) -> None:
    _write_text_atomic(
        session.chat_root / LAST_USED_FILE,
        f"{_validate_session_id(session.session_id)}\n",
    )


def get_session(
    chat_key: str,
    session_id: str | None = None,
    workspace: Path | None = None,
) -> Session:
    chat_key = _validate_chat_key(chat_key)
    session_id = _validate_session_id(
        session_id or _get_last_used_session_id(chat_key) or str(uuid4())
    )
    session = Session(chat_key=chat_key, session_id=session_id)
    session_dir = session.session_dir
    messages_path = session.messages_path
    # comment: load saved state, normalize it, then ensure the workspace and message file exist.
    messages_payload = (
        json.loads(messages_path.read_text(encoding="utf-8"))
        if messages_path.exists()
        else {}
    )
    messages_json = _normalized_messages_json(
        chat_key,
        session_id,
        session_dir,
        messages_payload,
        workspace,
    )
    session_dir.mkdir(parents=True, exist_ok=True)
    workspace_path = Path(messages_json["workspace"])
    workspace_path.mkdir(parents=True, exist_ok=True)
    # comment: new workspaces should always have AGENTS.md so long-term notes have a stable home.
    (workspace_path / "AGENTS.md").touch(exist_ok=True)
    _write_text_atomic(
        messages_path,
        json.dumps(messages_json, indent=2, ensure_ascii=False) + "\n",
    )
    set_last_used(session)
    return session


def set_session_name(session: Session, name: str) -> None:
    new_session_id = _validate_session_id(name.strip() or str(uuid4()))
    if new_session_id == session.session_id:
        return

    old_session_dir = session.session_dir
    new_session_dir = session.chat_root / new_session_id
    if new_session_dir.exists():
        raise ValueError(f"Session already exists: {new_session_id}")

    try:
        was_last_used = (session.chat_root / LAST_USED_FILE).read_text(
            encoding="utf-8"
        ).strip() == session.session_id
    except OSError:
        # comment: old histories may not have an explicit last-used marker yet.
        was_last_used = False

    # comment: rename the whole folder so the session id is the user-visible name.
    old_session_dir.rename(new_session_dir)
    # comment: mutate the current session so active streams keep writing to the renamed folder.
    session.session_id = new_session_id
    if was_last_used:
        # comment: preserve the current-session marker across rename.
        set_last_used(session)


def _session_label(session_id: str, messages_path: Path) -> str:
    updated_at = datetime.fromtimestamp(messages_path.stat().st_mtime)
    return f"{session_id} - {updated_at.day} {updated_at:%b}"


def list_sessions(chat_key: str) -> list[dict[str, str]]:
    chat_key = _validate_chat_key(chat_key)
    last_used = _get_last_used_session_id(chat_key)
    message_paths = list(_chat_root(chat_key).glob(f"*/{MESSAGES_FILE}"))
    message_paths.sort(
        key=lambda path: (path.parent.name != last_used, -path.stat().st_mtime)
    )
    return [
        {
            "id": path.parent.name,
            "name": _session_label(path.parent.name, path),
        }
        for path in message_paths
    ]


def get_messages(session: Session) -> MessagesJson:
    messages_path = session.messages_path
    payload = (
        json.loads(messages_path.read_text(encoding="utf-8"))
        if messages_path.exists()
        else {}
    )
    if not payload:
        get_session(chat_key=session.chat_key, session_id=session.session_id)
        payload = json.loads(messages_path.read_text(encoding="utf-8"))
    return _normalized_messages_json(
        session.chat_key,
        session.session_id,
        session.session_dir,
        payload,
    )


def get_last_usage(session: Session) -> dict[str, Any] | None:
    for item in reversed(get_messages(session)["messages"]):
        if not isinstance(item, dict):
            continue
        usage = item.get("usage")
        if isinstance(usage, dict):
            return cast(dict[str, Any], usage)
    return None


async def compact_message_history(session: Session) -> bool:
    config = build_config()
    messages_json = get_messages(session)
    if not messages_json["messages"]:
        # comment: nothing to compact for a new/empty session.
        return False

    workspace = Path(messages_json["workspace"])
    instructions = get_system_instructions(config, session.chat_key, workspace)
    input_items = trim_input(
        messages_json["messages"],
        replace_unavailable_uploads=uses_chatgpt_oauth(config),
    )
    client = get_openai_client(config)
    try:
        compacted = await client.responses.compact(
            model=config.openai_model,
            input=cast(Any, input_items),
            instructions=instructions or omit,
            prompt_cache_key=messages_json["id"],
        )
    finally:
        await client.close()

    output: MessageHistory = []
    for raw_item in compacted.output:
        item = raw_item.to_dict() if hasattr(raw_item, "to_dict") else raw_item
        if not isinstance(item, dict):
            # comment: compaction output should be a response input item.
            raise TypeError(f"Expected compacted item dict, got {type(item).__name__}")
        if item.get("type") == "compaction":
            # comment: standalone compact output must be replayed as one canonical window.
            item = {**item, STANDALONE_COMPACTION_KEY: True}
        output.append(item)
    if not output:
        # comment: avoid losing history if the compaction endpoint returns no window.
        return False

    messages_json["system_prompt"] = instructions
    # comment: create an archive snapshot file before replacing messages.json.
    _write_text_atomic(
        session.session_dir / f"messages.archive.{uuid4().hex}.json",
        json.dumps(messages_json, indent=2, ensure_ascii=False) + "\n",
    )
    messages_json["messages"] = output
    set_messages(session, messages_json)
    return True


def set_messages(session: Session, messages_json: MessagesJson) -> None:
    _write_text_atomic(
        session.messages_path,
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


def _output_text(response: Response, output: list[ResponseOutputItem]) -> str:
    parts: list[str] = []
    for item in output:
        if not isinstance(item, ResponseOutputMessage):
            continue
        text = "".join(
            part.text for part in item.content if isinstance(part, ResponseOutputText)
        ).strip()
        if text:
            parts.append(text)
    if parts:
        return "\n\n".join(parts)

    try:
        output_text = getattr(response, "output_text", "")
    except TypeError:
        # comment: OpenAI SDK output_text can crash when response.output is None.
        output_text = ""
    return output_text.strip() if isinstance(output_text, str) else ""


def _generated_image_markdown(
    output: list[ResponseOutputItem], workspace: Path
) -> list[str]:
    images_dir = workspace / GENERATED_IMAGES_DIR
    lines: list[str] = []

    for item in output:
        if not isinstance(item, ImageGenerationCall):
            continue
        result = item.result
        if not isinstance(result, str):
            continue

        images_dir.mkdir(parents=True, exist_ok=True)
        path = images_dir / f"{uuid4().hex}.png"
        path.write_bytes(base64.b64decode(result))
        lines.append(f"![Generated image]({path.relative_to(workspace).as_posix()})")

    return lines


def _append_display_output_text(messages: MessageHistory, text: str) -> None:
    part = {
        "type": "output_text",
        "text": text,
        "annotations": [],
        DISPLAY_ONLY_CONTENT_KEY: True,
    }
    for index in range(len(messages) - 1, -1, -1):
        item = messages[index]
        if item.get("type") == "message" and item.get("role") == "user":
            break
        if item.get("type") == "message" and item.get("role") == "assistant":
            content = item.get("content")
            if isinstance(content, list):
                content.append(part)
                return
    messages.append(
        {
            "type": "message",
            "role": "assistant",
            "content": [part],
        }
    )


def _append_response_output_text(output: list[ResponseOutputItem], text: str) -> None:
    for item in reversed(output):
        if not isinstance(item, ResponseOutputMessage):
            continue
        for part in reversed(item.content):
            if isinstance(part, ResponseOutputText):
                part.text = f"{part.text}{text}"
                return
        item.content.append(
            ResponseOutputText(type="output_text", text=text, annotations=[])
        )
        return

    output.append(
        ResponseOutputMessage(
            id=f"msg_{uuid4().hex}",
            type="message",
            role="assistant",
            status="completed",
            content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
        )
    )


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
        logger.info(
            "Skipping duplicate user turn; message_ids=%s",
            len(message_ids),
        )
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
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
    )
    messages_json["message_ids"].extend(fresh_message_ids)
    set_messages(session, messages_json)
    logger.info(
        "Appended user turn; attachments=%s message_ids=%s",
        len(attachments or ()),
        len(fresh_message_ids),
    )
    return True


def _session_tools(messages_json: MessagesJson) -> list[Tool]:
    workspace = Path(messages_json["workspace"])
    chat_key = messages_json["chat_key"]
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
    return tools


async def _get_streaming_reply(
    *,
    config: Config,
    instructions: str,
    input: MessageHistory,
    tools: list[Tool],
    prompt_cache_key: str,
) -> AsyncIterator[StreamingReplyItem]:
    if getattr(config, "openai_websocket", False) and (
        config.openai_api_key or config.openai_oauth
    ):
        async for item in websocket_streaming_reply(
            config,
            instructions=instructions,
            input=input,
            tools=tools,
            prompt_cache_key=prompt_cache_key,
        ):
            yield item
        return

    async for item in get_streaming_reply(
        config,
        instructions=instructions,
        input=input,
        tools=tools,
        prompt_cache_key=prompt_cache_key,
    ):
        yield item


async def get_answer_streaming(
    session: Session,
) -> AsyncIterator[StreamingReplyItem]:
    logger.info("Starting answer stream")
    config = build_config()
    messages_json = get_messages(session)
    workspace = Path(messages_json["workspace"])
    tools = _session_tools(messages_json)

    instructions = get_system_instructions(config, session.chat_key, workspace)
    if messages_json["system_prompt"] != instructions:
        # comment: keep a debug snapshot without trusting stale prompts from older app versions.
        messages_json["system_prompt"] = instructions
        set_messages(session, messages_json)

    new_message_index = len(messages_json["messages"])
    async for event in _get_streaming_reply(
        config=config,
        instructions=instructions,
        input=messages_json["messages"],
        tools=tools,
        prompt_cache_key=messages_json["id"],
    ):
        if event.type == "response.completed":
            event = cast(ResponseCompletedEvent, event)
            output = cast(
                list[ResponseOutputItem],
                event.response.output or getattr(event.response, "codex_output", []),
            )
            image_markdown = "\n\n".join(_generated_image_markdown(output, workspace))
            if image_markdown:
                image_markdown = f"\n\n{image_markdown}"
                _append_display_output_text(messages_json["messages"], image_markdown)
                _append_response_output_text(output, image_markdown)
                yield cast(
                    StreamingReplyItem,
                    SimpleNamespace(
                        type="response.output_text.delta", delta=image_markdown
                    ),
                )
        if event.type in {"function_call_output", "response.completed"}:
            created_at = datetime.now().astimezone().isoformat(timespec="seconds")
            for item in messages_json["messages"][new_message_index:]:
                item.setdefault("created_at", created_at)
            set_messages(session, messages_json)
        yield event
    logger.info("Finished answer stream")


async def prewarm_openai_websocket(session: Session) -> None:
    config = build_config()
    if not getattr(config, "openai_websocket", False):
        return
    if not (config.openai_api_key or config.openai_oauth):
        return
    messages_json = get_messages(session)
    workspace = Path(messages_json["workspace"])
    try:
        await websocket_prewarm(
            config,
            instructions=get_system_instructions(config, session.chat_key, workspace),
            input=messages_json["messages"],
            tools=_session_tools(messages_json),
            prompt_cache_key=messages_json["id"],
        )
    except Exception:
        logger.exception("OpenAI websocket prewarm failed")
        raise


async def get_answer(session: Session) -> str:
    answer = ""
    async for event in get_answer_streaming(session):
        if event.type == "response.completed":
            completed = cast(ResponseCompletedEvent, event)
            output = cast(
                list[ResponseOutputItem],
                completed.response.output
                or getattr(completed.response, "codex_output", []),
            )
            answer = _output_text(completed.response, output)
    return answer
