from collections.abc import Iterable

from faltoobot.chat.entries import Entry, queue_preview, visible_content

KIND_LABELS = {
    "you": "You",
    "bot": "Faltoobot",
    "thinking": "Thinking",
    "tool": "Tool",
    "error": "Error",
    "opened": "Opened",
    "meta": "Info",
}


def prefix_block(label: str, text: str) -> str:
    lines = text.splitlines() or [""]
    head = f"{label}: {lines[0]}".rstrip()
    tail = [f"    {line}".rstrip() for line in lines[1:]]
    return "\n".join([head, *tail]).strip()


def entry_text(entry: Entry) -> str:
    content = visible_content(entry.kind, entry.content).strip()
    if not content:
        return ""
    if entry.kind == "banner":
        return content
    if label := KIND_LABELS.get(entry.kind):
        return prefix_block(label, content)
    return content


def transcript_text(entries: Iterable[Entry]) -> str:
    parts = [entry_text(entry) for entry in entries]
    return "\n\n".join(part for part in parts if part)


def queue_text(items: Iterable[tuple[str, bool]]) -> str:
    lines = [
        f"{'□' if paused else '☑︎'} {queue_preview(content)}"
        for content, paused in items
    ]
    return "\n".join(lines) or "No queued prompts"


def status_line(status: str, *, replying: bool, queued: int) -> str:
    parts = [status]
    if replying:
        parts.append("replying")
    if queued:
        parts.append(f"queued {queued}")
    return "  ".join(parts)
