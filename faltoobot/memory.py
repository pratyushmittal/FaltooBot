import re
from pathlib import Path
from typing import TypedDict
from urllib.parse import quote


class MemoryEntry(TypedDict):
    id: int
    text: str


_BULLET_PREFIX = re.compile(r"^(?:[-*]|\d+\.)\s+")


def _memory_entry(entry_id: int, text: str) -> MemoryEntry:
    return {
        "id": entry_id,
        "text": text,
    }


def _memory_dir(root: Path) -> Path:
    return root / "memory"


def memory_file_path(root: Path, chat_key: str) -> Path:
    encoded = quote(chat_key, safe="@._-")
    return _memory_dir(root) / f"{encoded}.md"


def _coerce_entries(lines: list[str]) -> list[MemoryEntry]:
    entries: list[MemoryEntry] = []
    for line in lines:
        cleaned = " ".join(_BULLET_PREFIX.sub("", line.strip()).split()).strip()
        if not cleaned:
            continue
        entries.append(_memory_entry(len(entries) + 1, cleaned))
    return entries


def list_memory_entries(root: Path, chat_key: str) -> list[MemoryEntry]:
    path = memory_file_path(root, chat_key)
    if not path.exists():
        return []
    return _coerce_entries(path.read_text(encoding="utf-8").splitlines())


def render_memory_prompt(root: Path, chat_key: str) -> str | None:
    entries = list_memory_entries(root, chat_key)
    if not entries:
        return None
    bullets = "\n".join(f"- {entry['text']}" for entry in entries)
    return f"User Memory (Always Remember):\n{bullets}"


def format_memory_list(root: Path, chat_key: str) -> str:
    entries = list_memory_entries(root, chat_key)
    if not entries:
        return "Nothing to remember yet."
    lines = ["Here are the things I remember:"]
    lines.extend(f"{entry['id']}. {entry['text']}" for entry in entries)
    return "\n".join(lines)
