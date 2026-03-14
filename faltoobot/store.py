import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

SessionKind = Literal["cli", "whatsapp"]
Role = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class Turn:
    role: Role
    content: str
    created_at: str
    items: tuple[dict[str, Any], ...] = ()
    usage: dict[str, Any] | None = None
    instructions: str | None = None


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


def ensure_sessions_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def session_root(sessions_dir: Path, session_id: str) -> Path:
    return ensure_sessions_dir(sessions_dir) / session_id


def session_name(kind: SessionKind, value: str) -> str:
    return f"CLI {value}" if kind == "cli" else f"WhatsApp {value}"


def messages_path(root: Path) -> Path:
    return root / "messages.json"


def workspace_path(root: Path) -> Path:
    return root / "workspace"


def session_payload(session: Session) -> dict[str, Any]:
    return {
        "id": session.id,
        "name": session.name,
        "kind": session.kind,
        "chat_key": session.chat_key,
        "workspace": str(session.workspace),
        "processed_message_ids": list(session.processed_message_ids),
        "messages": [
            turn_payload(turn)
            for turn in session.messages
        ],
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
            items=tuple(entry for entry in item_items(item) if isinstance(entry, dict)),
            usage=item_usage(item),
            instructions=item_instructions(item),
        )
        for item in raw_messages
        if isinstance(raw_messages, list)
        and isinstance(item, dict)
        and item.get("role") in {"user", "assistant"}
        and isinstance(item.get("content"), str)
        and isinstance(item.get("created_at"), str)
    )
    raw_processed = payload.get("processed_message_ids")
    processed = tuple(
        message_id for message_id in raw_processed if isinstance(message_id, str)
    ) if isinstance(raw_processed, list) else ()
    return Session(
        id=str(payload.get("id") or root.name),
        name=str(payload.get("name") or root.name),
        kind="cli" if payload.get("kind") == "cli" else "whatsapp",
        chat_key=payload.get("chat_key") if isinstance(payload.get("chat_key"), str) else None,
        root=root,
        messages_file=messages_path(root),
        workspace=Path(payload["workspace"])
        if isinstance(payload.get("workspace"), str)
        else workspace_path(root),
        processed_message_ids=processed,
        messages=messages,
    )


def item_items(item: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = item.get("items")
    if not isinstance(raw_items, list):
        return []
    return [entry for entry in raw_items if isinstance(entry, dict)]


def item_usage(item: dict[str, Any]) -> dict[str, Any] | None:
    usage = item.get("usage")
    return usage if isinstance(usage, dict) else None


def item_instructions(item: dict[str, Any]) -> str | None:
    instructions = item.get("instructions")
    return instructions if isinstance(instructions, str) else None


def load_session(root: Path) -> Session:
    session = build_session(root, read_json(messages_path(root)))
    session.workspace.mkdir(parents=True, exist_ok=True)
    return session


def save_session(session: Session) -> Session:
    session.root.mkdir(parents=True, exist_ok=True)
    session.workspace.mkdir(parents=True, exist_ok=True)
    write_json(session.messages_file, session_payload(session))
    return session


def index_path(sessions_dir: Path) -> Path:
    return ensure_sessions_dir(sessions_dir) / "index.json"


def load_index(sessions_dir: Path) -> dict[str, str]:
    return {
        key: value
        for key, value in read_json(index_path(sessions_dir)).items()
        if isinstance(key, str) and isinstance(value, str)
    }


def save_index(sessions_dir: Path, index: dict[str, str]) -> None:
    write_json(index_path(sessions_dir), index)


def indexed_session(sessions_dir: Path, key: str) -> Session | None:
    session_id = load_index(sessions_dir).get(key)
    if not session_id:
        return None
    root = session_root(sessions_dir, session_id)
    return load_session(root) if messages_path(root).exists() else None


def save_indexed_session(sessions_dir: Path, key: str, session: Session) -> Session:
    save_index(sessions_dir, {**load_index(sessions_dir), key: session.id})
    return session


def create_session(
    sessions_dir: Path,
    name: str,
    kind: SessionKind,
    chat_key: str | None = None,
    workspace: Path | None = None,
) -> Session:
    root = session_root(sessions_dir, str(uuid4()))
    session = Session(
        id=root.name,
        name=name,
        kind=kind,
        chat_key=chat_key,
        root=root,
        messages_file=messages_path(root),
        workspace=workspace or workspace_path(root),
        processed_message_ids=(),
        messages=(),
    )
    return save_session(session)


def create_cli_session(sessions_dir: Path, name: str, workspace: Path) -> Session:
    return create_session(sessions_dir, name=name, kind="cli", workspace=workspace)


def cli_session_key(workspace: Path) -> str:
    return f"cli:{workspace.resolve()}"


def cli_session(sessions_dir: Path, name: str, workspace: Path) -> Session:
    return save_indexed_session(
        sessions_dir,
        cli_session_key(workspace),
        create_cli_session(sessions_dir, name, workspace),
    )


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
    return save_indexed_session(sessions_dir, chat_key, session)


def session_items(session: Session) -> list[dict[str, Any]]:
    return [
        item
        for turn in session.messages
        for item in turn_items(turn)
    ]


def turn_items(turn: Turn) -> list[dict[str, Any]]:
    if turn.role == "assistant" and turn.items:
        return list(turn.items)
    return [{"type": "message", "role": turn.role, "content": turn.content}]


def last_instructions(session: Session) -> str | None:
    for turn in reversed(session.messages):
        if turn.instructions:
            return turn.instructions
    return None


def add_turn(
    session: Session,
    role: Role,
    content: str,
    items: list[dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
    instructions: str | None = None,
) -> Session:
    next_instructions = instructions if isinstance(instructions, str) else None
    if role == "assistant" and next_instructions == last_instructions(session):
        next_instructions = None
    return save_session(
        replace(
            session,
            messages=(
                *session.messages,
                Turn(
                    role=role,
                    content=content,
                    created_at=now(),
                    items=tuple(item for item in (items or []) if isinstance(item, dict)),
                    usage=usage if isinstance(usage, dict) else None,
                    instructions=next_instructions,
                ),
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
