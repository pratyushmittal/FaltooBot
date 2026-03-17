import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SlashState:
    snapshot: tuple[tuple[str, str], ...] = ()
    dismissed_query: str | None = None


@dataclass(slots=True)
class QueueState:
    snapshot: tuple[tuple[str, bool], ...] = ()
    selected_snapshot: int | None = None
    selected: int | None = None
    drag_index: int | None = None


@dataclass(slots=True)
class TranscriptState:
    snapshot: tuple[tuple[str, str, bool], ...] = ()
    blocks: list[Any] = field(default_factory=list)
    stream_block: Any | None = None
    follow: bool = True
    settle_task: asyncio.Task[None] | None = None
