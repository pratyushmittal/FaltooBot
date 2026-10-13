"""Hand a chat over to another backend through a summary and the raw history files.

The current backend writes the summary without touching its session. A new session on
the target backend starts with that summary and a note listing the previous session's
raw files, which the new backend searches with its own tools when the summary missed
something. The old session is never modified; handing back is the way to roll back.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from faltoobot import claude_cli, claude_transcript, sessions
from faltoobot.config import BACKEND_OPTIONS
from faltoobot.gpt_utils import MessageHistory

HANDOFF_DIR = "handoff"
HANDOFF_PROMPT = """
This chat is being handed over to another assistant that cannot see this conversation.
Write a hand-off document in Markdown with exactly these sections, and write "none"
under a section that has nothing. Run commands where that helps you verify a fact,
such as `crontab -l`. Do not invent anything; mark facts you could not verify with
"(from memory)".

## The user
Who they are, preferences, tone, language.

## Standing commitments
Reminders, scheduled jobs, monitors and background sub-agents, with exact commands,
schedules, paths and sub-agent follow-up ids.

## Open threads
Promises and tasks not finished yet, with their next step.

## Important facts and decisions
What was learned or decided that still matters.

## Workspace
Files created and where notes live.

## Recent conversation
The last ten exchanges, condensed but faithful.
""".strip()
VERIFY_PROMPT = (
    "Read the hand-off above. Then reply to the user with a short recap of where "
    "things stand and list the reminders and background tasks you are now "
    "responsible for."
)


@dataclass
class Handoff:
    document: Path
    session: sessions.Session | None = None
    verification: str = ""


def _now() -> datetime:
    return datetime.now().astimezone()


def current_session(chat_key: str) -> sessions.Session:
    session_id = sessions._get_last_used_session_id(chat_key)
    if session_id is None:
        raise ValueError(f"No session found for {chat_key}")
    return sessions.Session(chat_key=chat_key, session_id=session_id)


def raw_history_files(
    session: sessions.Session, old: sessions.MessagesJson
) -> list[Path]:
    """The files that hold the whole previous conversation, oldest first."""
    if sessions.get_backend(old) == "claude":
        path = claude_transcript.transcript_path(old["claude_session_id"])
        return [path] if path else []
    archives = sorted(
        session.session_dir.glob("messages.archive.*.json"),
        key=lambda path: path.stat().st_mtime,
    )
    return [*archives, session.messages_path]


# --- summary -----------------------------------------------------------------------


def _last_assistant_text(items: MessageHistory) -> str:
    for item in reversed(items):
        if item.get("type") == "message" and item.get("role") == "assistant":
            content = item.get("content")
            if isinstance(content, str):
                return content
            return "".join(
                str(part.get("text") or "")
                for part in content or []
                if isinstance(part, dict)
            )
    return ""


async def summarize(
    session: sessions.Session, messages_json: sessions.MessagesJson
) -> str:
    """Ask the session's own backend for the hand-off, leaving the session unchanged."""
    config = sessions.build_config()
    workspace = Path(messages_json["workspace"])
    if sessions.get_backend(messages_json) == "claude":
        if not messages_json["claude_session_id"]:
            return "This chat has no Claude conversation yet."
        return await claude_cli.ask_fork(
            workspace, config, messages_json["claude_session_id"], HANDOFF_PROMPT
        )
    # comment: a copy, so the hand-off question never lands in the old history.
    history = [
        *messages_json["messages"],
        {"type": "message", "role": "user", "content": HANDOFF_PROMPT},
    ]
    async for _ in sessions._get_streaming_reply(
        config=config,
        instructions=sessions.get_system_instructions(
            config, session.chat_key, workspace
        ),
        input=history,
        tools=sessions._session_tools(messages_json),
        prompt_cache_key=messages_json["id"],
    ):
        pass
    return _last_assistant_text(history)


# --- switch --------------------------------------------------------------------------


def _note(
    backend: str,
    created: datetime,
    previous: sessions.Session,
    document: Path,
    files: list[Path],
) -> str:
    # comment: the note doubles as the lineage record; nothing else stores it.
    listed = "\n".join(f"- {path}" for path in files) or "- (none found)"
    return (
        f"This chat was handed over from the {backend} backend on "
        f"{created:%Y-%m-%d} (previous session: {previous.session_dir}).\n"
        f"Hand-off summary: {document}\n"
        "The whole previous conversation is in these raw files, oldest first. Search "
        "them with grep or jq whenever the user refers to something you do not know:\n"
        f"{listed}\n"
        "Add anything you learn from them that matters long-term to AGENTS.md."
    )


def _start_session(  # noqa: PLR0913
    previous: sessions.Session,
    old: sessions.MessagesJson,
    to: str,
    paths: Handoff,
    document: str,
    created: datetime,
) -> sessions.Session:
    files = raw_history_files(previous, old)
    new = sessions.get_session(
        previous.chat_key,
        session_id=str(uuid4()),
        workspace=Path(old["workspace"]),
    )
    messages_json = sessions.get_messages(new)
    messages_json["backend"] = to
    # comment: WhatsApp deduplicates by message id, so the ids must carry over.
    messages_json["message_ids"] = list(old["message_ids"])
    messages_json["messages"] = [
        *sessions._get_new_message_history(previous.chat_key),
        sessions.developer_message_item(
            _note(old["backend"], created, previous, paths.document, files)
        ),
        sessions.developer_message_item(f"# Hand-off document\n\n{document}"),
    ]
    sessions.set_messages(new, messages_json)
    return new


async def hand_off(
    chat_key: str, *, to: str, dry_run: bool = False, verify: bool = True
) -> Handoff:
    if to not in BACKEND_OPTIONS:
        raise ValueError(
            f"Unknown backend {to!r}; choose from {', '.join(BACKEND_OPTIONS)}"
        )
    previous = current_session(chat_key)
    old = sessions.get_messages(previous)
    created = _now()
    summary = await summarize(previous, old)
    folder = previous.session_dir / HANDOFF_DIR
    folder.mkdir(parents=True, exist_ok=True)
    paths = Handoff(document=folder / f"{created:%Y%m%d-%H%M%S}.md")
    document = f"# Hand-off\n\n{summary.strip()}\n"
    paths.document.write_text(document, encoding="utf-8")
    if dry_run:
        return paths
    if sessions.get_backend(old) == "claude":
        await claude_cli.stop(previous)
    paths.session = _start_session(previous, old, to, paths, document, created)
    if verify:
        await sessions.append_user_turn(paths.session, question=VERIFY_PROMPT)
        try:
            paths.verification = await sessions.get_answer(paths.session)
        except Exception as exc:  # comment: the switch already happened; say so.
            paths.verification = f"The hand-off is done, but the recap failed: {exc}"
    return paths


def whatsapp_chat_keys() -> list[str]:
    root = sessions.app_root() / "sessions"
    if not root.is_dir():
        return []
    return sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir()
        and not path.name.startswith(("code@", "sub-agent@"))
        and sessions._get_last_used_session_id(path.name)
    )
