"""Claude CLI backend: drives a long-lived `claude -p --input-format stream-json` process.

Phase 1 ships only the seam; the bridge itself arrives in phase 2.
"""

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from faltoobot.config import Config
from faltoobot.gpt_utils import StreamingReplyItem

if TYPE_CHECKING:
    from faltoobot.sessions import MessagesJson, Session

NOT_READY = "The Claude backend is not available yet (phase 2 of claude-plan.md)."


async def stream_reply(
    session: "Session",
    messages_json: "MessagesJson",
    *,
    config: Config,
    instructions: str,
) -> AsyncIterator[StreamingReplyItem]:
    raise RuntimeError(NOT_READY)
    yield  # type: ignore[misc]  # comment: keeps this an async generator for callers.


async def compact(session: "Session", messages_json: "MessagesJson") -> bool:
    raise ValueError(NOT_READY)
