import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, cast

from openai._models import construct_type_unchecked
from openai.types.responses import FunctionToolParam, ResponsesServerEvent
from websockets.asyncio.client import connect as websocket_connect

from faltoobot.config import Config
from faltoobot.openai_auth import get_openai_client_options, uses_chatgpt_oauth
from faltoobot.gpt_utils import (
    COMPACT_THRESHOLD,
    FunctionToolCallItem,
    MessageHistory,
    StreamingReplyItem,
    Tool,
    _remember_response_event,
    _to_message_item,
    _tool_calls_from_response,
    _yield_tool_results,
    trim_input,
)

RESPONSES_WEBSOCKET_URL = "wss://api.openai.com/v1/responses"


async def streaming_reply(  # noqa: PLR0913
    config: Config,
    *,
    instructions: str,
    input: MessageHistory,
    tools: list[FunctionToolParam | dict[str, Any]],
    tools_by_name: dict[str, Tool],
    prompt_cache_key: str | None,
) -> AsyncIterator[StreamingReplyItem]:
