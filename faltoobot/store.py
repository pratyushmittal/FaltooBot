import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

SessionKind = Literal["cli", "whatsapp"]
Role = Literal["user", "assistant"]
MESSAGES_FILE = "messages.json"
WORKSPACE_DIR = "workspace"
INDEX_FILE = "index.json"


@dataclass(frozen=True, slots=True)
class Turn:
    role: Role
    content: str
    created_at: str
    items: tuple[dict[str, Any], ...] = ()
    usage: dict[str, Any] | None = None
    instructions: str | None = None


@dataclass(slots=True)
class QueuedPrompt:
    content: str
    paused: bool = False


@dataclass(frozen=True, slots=True)
class Session:
    id: str
    name: str
    kind: SessionKind
    chat_key: str | None
    root: Path
    messages_file: Path
    workspace: Path
    processed_message_ids: tuple[str, ...]
    messages: tuple[Turn, ...]
    queued_prompts: tuple[QueuedPrompt, ...]


def ensure_sessions_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def session_name(kind: SessionKind, value: str) -> str:
    return f"CLI {value}" if kind == "cli" else f"WhatsApp {value}"


def session_payload(session: Session) -> dict[str, Any]:
    return {
        "id": session.id,
        "name": session.name,
        "kind": session.kind,
        "chat_key": session.chat_key,
        "workspace": str(session.workspace),
        "processed_message_ids": list(session.processed_message_ids),
        "queued_prompts": [queued_prompt_payload(prompt) for prompt in session.queued_prompts],
        "messages": [turn_payload(turn) for turn in session.messages],
    }


def turn_payload(turn: Turn) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "role": turn.role,
        "content": turn.content,
        "created_at": turn.created_at,
    }
    if turn.items:
        payload["items"] = list(turn.items)
    if turn.usage:
        payload["usage"] = turn.usage
    if turn.instructions:
        payload["instructions"] = turn.instructions
    return payload


def queued_prompt_payload(prompt: QueuedPrompt) -> dict[str, Any]:
    return {"content": prompt.content, "paused": prompt.paused}


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build_session(root: Path, payload: dict[str, Any]) -> Session:
    raw_messages = payload.get("messages")
    messages = tuple(
        Turn(
            role=item["role"],
            content=item["content"],
            created_at=item["created_at"],
            items=tuple(
                entry for entry in item.get("items", []) if isinstance(entry, dict)
            )
            if isinstance(item.get("items"), list)
            else (),
            usage=item.get("usage") if isinstance(item.get("usage"), dict) else None,
            instructions=(
                item.get("instructions") if isinstance(item.get("instructions"), str) else None
            ),
        )
        for item in raw_messages
        if isinstance(raw_messages, list)
        and isinstance(item, dict)
        and item.get("role") in {"user", "assistant"}
        and isinstance(item.get("content"), str)
        and isinstance(item.get("created_at"), str)
    )
    raw_processed = payload.get("processed_message_ids")
    processed = (
        tuple(message_id for message_id in raw_processed if isinstance(message_id, str))
        if isinstance(raw_processed, list)
        else ()
    )
    queued_prompts = tuple(
        QueuedPrompt(content=item["content"], paused=bool(item.get("paused")))
        for item in payload.get("queued_prompts", [])
        if isinstance(item, dict) and isinstance(item.get("content"), str)
    )
    workspace = payload.get("workspace")
    return Session(
        id=str(payload.get("id") or root.name),
        name=str(payload.get("name") or root.name),
        kind="cli" if payload.get("kind") == "cli" else "whatsapp",
        chat_key=payload.get("chat_key") if isinstance(payload.get("chat_key"), str) else None,
        root=root,
        messages_file=root / MESSAGES_FILE,
        workspace=Path(workspace) if isinstance(workspace, str) else root / WORKSPACE_DIR,
        processed_message_ids=processed,
        messages=messages,
        queued_prompts=queued_prompts,
    )


def load_session(root: Path) -> Session:
    session = build_session(root, read_json(root / MESSAGES_FILE))
    session.workspace.mkdir(parents=True, exist_ok=True)
    return session


def save_session(session: Session) -> Session:
    session.root.mkdir(parents=True, exist_ok=True)
    session.workspace.mkdir(parents=True, exist_ok=True)
    write_json(session.messages_file, session_payload(session))
    return session


def indexed_session(sessions_dir: Path, key: str) -> Session | None:
    index_path = ensure_sessions_dir(sessions_dir) / INDEX_FILE
    index = {
        entry_key: value
        for entry_key, value in read_json(index_path).items()
        if isinstance(entry_key, str) and isinstance(value, str)
    }
    session_id = index.get(key)
    if not session_id:
        return None
    root = ensure_sessions_dir(sessions_dir) / session_id
    return load_session(root) if (root / MESSAGES_FILE).exists() else None


def create_session(
    sessions_dir: Path,
    name: str,
    kind: SessionKind,
    chat_key: str | None = None,
    workspace: Path | None = None,
) -> Session:
    root = ensure_sessions_dir(sessions_dir) / str(uuid4())
    session = Session(
        id=root.name,
        name=name,
        kind=kind,
        chat_key=chat_key,
        root=root,
        messages_file=root / MESSAGES_FILE,
        workspace=workspace or root / WORKSPACE_DIR,
        processed_message_ids=(),
        messages=(),
        queued_prompts=(),
    )
    return save_session(session)


def cli_session_key(workspace: Path) -> str:
    return f"cli:{workspace.resolve()}"


def cli_session(sessions_dir: Path, name: str, workspace: Path) -> Session:
    session = create_session(sessions_dir, name=name, kind="cli", workspace=workspace)
    index_path = ensure_sessions_dir(sessions_dir) / INDEX_FILE
    index = {
        entry_key: value
        for entry_key, value in read_json(index_path).items()
        if isinstance(entry_key, str) and isinstance(value, str)
    }
    write_json(index_path, {**index, cli_session_key(workspace): session.id})
    return session


def existing_cli_session(sessions_dir: Path, workspace: Path) -> Session | None:
    return indexed_session(sessions_dir, cli_session_key(workspace))


def whatsapp_session(sessions_dir: Path, chat_key: str) -> Session:
    if session := indexed_session(sessions_dir, chat_key):
        return session
    session = create_session(
        sessions_dir,
        name=session_name("whatsapp", chat_key),
        kind="whatsapp",
        chat_key=chat_key,
    )
    index_path = ensure_sessions_dir(sessions_dir) / INDEX_FILE
    index = {
        entry_key: value
        for entry_key, value in read_json(index_path).items()
        if isinstance(entry_key, str) and isinstance(value, str)
    }
    write_json(index_path, {**index, chat_key: session.id})
    return session


def session_items(session: Session) -> list[dict[str, Any]]:
    return [item for turn in session.messages for item in turn_items(turn)]


def turn_items(turn: Turn) -> list[dict[str, Any]]:
    items = list(turn.items)
    if not items:
        return [{"type": "message", "role": turn.role, "content": turn.content}]
    has_message = any(
        item.get("type") == "message" and item.get("role") == turn.role
        for item in items
        if isinstance(item, dict)
    )
    if turn.content and not has_message:
        items.append({"type": "message", "role": turn.role, "content": turn.content})
    return items


def last_instructions(session: Session) -> str | None:
    for turn in reversed(session.messages):
        if turn.instructions:
            return turn.instructions
    return None


def assistant_turn(
    session: Session,
    content: str,
    items: list[dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
    instructions: str | None = None,
    created_at: str | None = None,
) -> Turn:
    next_instructions = instructions if isinstance(instructions, str) else None
    if next_instructions == last_instructions(session):
        next_instructions = None
    return Turn(
        role="assistant",
        content=content,
        created_at=created_at or now(),
        items=tuple(item for item in (items or []) if isinstance(item, dict)),
        usage=usage if isinstance(usage, dict) else None,
        instructions=next_instructions,
    )


def add_turn(
    session: Session,
    role: Role,
    content: str,
    items: list[dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
    instructions: str | None = None,
) -> Session:
    turn = (
        assistant_turn(session, content, items, usage, instructions)
        if role == "assistant"
        else Turn(
            role=role,
            content=content,
            created_at=now(),
            items=tuple(item for item in (items or []) if isinstance(item, dict)),
            usage=usage if isinstance(usage, dict) else None,
            instructions=instructions if isinstance(instructions, str) else None,
        )
    )
    return save_session(replace(session, messages=(*session.messages, turn)))


def sync_assistant_turn(
    session: Session,
    content: str,
    items: list[dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
    instructions: str | None = None,
) -> Session:
    last = session.messages[-1] if session.messages else None
    base = replace(session, messages=session.messages[:-1]) if last and last.role == "assistant" else session
    turn = assistant_turn(
        base,
        content,
        items,
        usage,
        instructions,
        last.created_at if last and last.role == "assistant" else None,
    )
    return save_session(replace(session, messages=(*base.messages, turn)))


def replace_queued_prompts(
    session: Session,
    queued_prompts: list[QueuedPrompt] | tuple[QueuedPrompt, ...],
) -> Session:
    return save_session(
        replace(
            session,
            queued_prompts=tuple(
                prompt
                for prompt in queued_prompts
                if isinstance(prompt, QueuedPrompt) and prompt.content.strip()
            ),
        )
    )


def reserve_message(session: Session, message_id: str) -> tuple[Session, bool]:
    if message_id in session.processed_message_ids:
        return session, False
    updated = replace(
        session,
        processed_message_ids=(*session.processed_message_ids, message_id),
    )
    return save_session(updated), True


def reset_session(session: Session) -> Session:
    return save_session(replace(session, messages=()))
