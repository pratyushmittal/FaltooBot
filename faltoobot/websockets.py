import asyncio
import json
import logging
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, cast

import certifi
from openai._models import construct_type_unchecked
from openai.types.responses import (
    FunctionToolParam,
    ResponseCompletedEvent,
    ResponseOutputItem,
    ResponsesServerEvent,
)
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as websocket_connect

from faltoobot.config import Config
from faltoobot.gpt_utils import (
    COMPACT_THRESHOLD,
    MessageHistory,
    StreamingReplyItem,
    Tool,
    _callable_name,
    _cloud_tools,
    _remember_response_event,
    _request_extra_headers,
    _to_message_item,
    _tool_calls_from_response,
    _tool_result,
    get_tools_definition,
    trim_input,
)
from faltoobot.openai_auth import get_openai_client_options, uses_chatgpt_oauth

logger = logging.getLogger("faltoobot")
RESPONSES_WEBSOCKET_URL = "wss://api.openai.com/v1/responses"
RESPONSES_WEBSOCKET_BETA_HEADER = "responses_websockets=2026-02-06"
WEBSOCKET_CONNECT_TIMEOUT_SECONDS = 60.0
WEBSOCKET_MAX_SIZE_BYTES = 16 * 1024 * 1024
WEBSOCKET_PREWARM_RETRIES = 3
WEBSOCKET_STREAM_RETRIES = 5
WEBSOCKET_SESSIONS: dict[str, "OpenAIWebsocketSession"] = {}
HTTP_CLIENT_ERROR_MIN = 400
HTTP_CLIENT_ERROR_MAX = 499


async def _auth_headers(
    api_key: str | Callable[[], Awaitable[str]],
    default_headers: dict[str, str] | None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    token = api_key if isinstance(api_key, str) else await api_key()
    return {
        **(default_headers or {}),
        **(extra_headers or {}),
        "OpenAI-Beta": RESPONSES_WEBSOCKET_BETA_HEADER,
        "Authorization": f"Bearer {token}",
    }


def _websocket_url(base_url: str | None) -> str:
    if not base_url:
        return RESPONSES_WEBSOCKET_URL
    return f"wss://{base_url.removeprefix('https://').rstrip('/')}/responses"


def _parse_websocket_event(raw: str | bytes) -> ResponsesServerEvent:
    payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    # comment: parse the raw websocket JSON into OpenAI's typed event models.
    return construct_type_unchecked(
        value=payload, type_=cast(Any, ResponsesServerEvent)
    )


class PreviousResponseNotFoundError(RuntimeError):
    pass


class WebsocketConnectionLimitReachedError(RuntimeError):
    pass


class MissingPrewarmResponseIDError(RuntimeError):
    pass


def _int_error_code(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _error_field(error: object, key: str) -> object | None:
    if isinstance(error, dict):
        return cast(dict[str, object], error).get(key)
    # comment: OpenAI typed websocket errors expose fields as attributes.
    return getattr(error, key, None)


def _get_error_code(exc: Exception) -> int | None:
    code = _int_error_code(getattr(exc, "status_code", None))
    if code is not None:
        return code

    response = getattr(exc, "response", None)
    code = _int_error_code(getattr(response, "status_code", None))
    if code is not None:
        return code

    return _int_error_code(getattr(exc, "code", None))


def _is_client_error(exc: Exception) -> bool:
    error_code = _get_error_code(exc)
    return (
        error_code is not None
        and HTTP_CLIENT_ERROR_MIN <= error_code <= HTTP_CLIENT_ERROR_MAX
    )


def _raise_for_response_error(event: ResponsesServerEvent) -> None:
    if event.type != "error":
        return

    error = getattr(event, "error", None)
    code = str(
        _error_field(error, "code")
        or _error_field(error, "type")
        or getattr(event, "code", None)
        or "error"
    )
    message = (
        _error_field(error, "message")
        or getattr(event, "message", None)
        or error
        or "Unknown websocket error"
    )
    exception = f"OpenAI websocket {code}: {message}"
    if code == "previous_response_not_found":
        raise PreviousResponseNotFoundError(exception)
    if code == "websocket_connection_limit_reached":
        raise WebsocketConnectionLimitReachedError(exception)
    raise RuntimeError(exception)


def _get_payload(  # noqa: PLR0913
    config: Config,
    *,
    instructions: str,
    input_items: MessageHistory,
    tools: list[FunctionToolParam | dict[str, Any]],
    prompt_cache_key: str,
    previous_response_id: str | None,
    generate: bool | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "response.create",
        "model": config.openai_model,
        "input": cast(Any, input_items),
        "tools": tools,
        "store": False,
        "tool_choice": "auto",
        "parallel_tool_calls": True,
        "instructions": instructions,
        "reasoning": {"summary": "auto", "effort": config.openai_thinking},
        "include": ["reasoning.encrypted_content", "web_search_call.action.sources"],
        "context_management": [
            {"type": "compaction", "compact_threshold": COMPACT_THRESHOLD}
        ],
    }
    if generate is not None:
        # comment: warmup uses generate=false to avoid returning model output.
        payload["generate"] = generate
    payload["prompt_cache_key"] = prompt_cache_key
    if previous_response_id:
        payload["previous_response_id"] = previous_response_id
    if config.openai_fast:
        payload["service_tier"] = "priority"
    return payload


@dataclass
class OpenAIWebsocketSession:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    ws: ClientConnection | None = None
    previous_response_id: str | None = None
    input_index: int = 0
    stream_retry_count: int = 0


async def _close_session(session: OpenAIWebsocketSession) -> None:
    if session.ws is not None:
        logger.info("Closing OpenAI websocket")
        await session.ws.close()
    session.ws = None
    session.previous_response_id = None
    session.input_index = 0


async def _connect_session(
    session: OpenAIWebsocketSession,
    config: Config,
    *,
    prompt_cache_key: str,
) -> None:
    if session.ws is not None:
        return
    api_key, base_url, default_headers = get_openai_client_options(config)
    session.ws = await websocket_connect(
        _websocket_url(base_url),
        additional_headers=await _auth_headers(
            api_key,
            default_headers,
            _request_extra_headers(config, prompt_cache_key),
        ),
        max_size=WEBSOCKET_MAX_SIZE_BYTES,
        open_timeout=WEBSOCKET_CONNECT_TIMEOUT_SECONDS,
        ssl=ssl.create_default_context(cafile=certifi.where()),
    )


async def _prewarm_if_needed(  # noqa: PLR0913
    session: OpenAIWebsocketSession,
    config: Config,
    *,
    instructions: str,
    input: MessageHistory,
    tools: list[FunctionToolParam | dict[str, Any]],
    prompt_cache_key: str,
) -> None:
    if session.previous_response_id:
        return

    request_input = trim_input(
        input, replace_unavailable_uploads=uses_chatgpt_oauth(config)
    )
    logger.info("Starting OpenAI websocket prewarm; input_items=%s", len(request_input))
    for attempt in range(WEBSOCKET_PREWARM_RETRIES + 1):
        payload = _get_payload(
            config,
            instructions=instructions,
            input_items=request_input,
            tools=tools,
            prompt_cache_key=prompt_cache_key,
            previous_response_id=None,
            generate=False,
        )
        try:
            await _connect_session(session, config, prompt_cache_key=prompt_cache_key)
            completed_response_id: str | None = None
            async for item in _read_response(session, payload):
                if isinstance(item, _CompletedResponse):
                    completed_response_id = item.response_id
            if not completed_response_id:
                raise MissingPrewarmResponseIDError(
                    "OpenAI websocket prewarm completed without response id"
                )
            session.previous_response_id = completed_response_id
            session.input_index = len(input)
            logger.info(
                "OpenAI websocket prewarm complete; input_index=%s",
                session.input_index,
            )
            return
        except Exception as exc:
            await _close_session(session)
            if _is_client_error(exc):
                # comment: bad auth/payload will not succeed by retrying prewarm.
                raise
            if attempt < WEBSOCKET_PREWARM_RETRIES:
                logger.warning(
                    "Retrying OpenAI websocket prewarm (%s/%s): %s",
                    attempt + 1,
                    WEBSOCKET_PREWARM_RETRIES,
                    exc,
                )
                continue
            logger.warning("OpenAI websocket prewarm failed", exc_info=True)
            raise


@dataclass
class _CompletedResponse:
    event: ResponseCompletedEvent
    response_output: list[ResponseOutputItem]
    response_id: str | None


def _current_input(
    input: MessageHistory,
    session: OpenAIWebsocketSession,
    *,
    replace_unavailable_uploads: bool,
) -> MessageHistory:
    if session.input_index > len(input):
        # comment: stale websocket state must go through the prewarm gate again.
        raise PreviousResponseNotFoundError("OpenAI websocket input state is stale")
    return trim_input(
        input[session.input_index :],
        replace_unavailable_uploads=replace_unavailable_uploads,
    )


async def _read_response(
    session: OpenAIWebsocketSession,
    payload: dict[str, Any],
) -> AsyncIterator[StreamingReplyItem | _CompletedResponse]:
    response_output: list[ResponseOutputItem] = []
    completed_item: _CompletedResponse | None = None
    last_event_type: str | None = None
    ws = cast(ClientConnection, session.ws)
    await ws.send(json.dumps(payload))

    async for raw in ws:
        event = _parse_websocket_event(raw)
        _raise_for_response_error(event)
        last_event_type = event.type

        response_id = _remember_response_event(event, response_output)
        if event.type == "response.completed":
            # comment: docs say response.completed means model response is complete.
            # Without break, async-for waits for the continuous websocket to close.
            completed_item = _CompletedResponse(
                cast(ResponseCompletedEvent, event), response_output, response_id
            )
            break
        yield event

    if last_event_type != "response.completed" or completed_item is None:
        raise ValueError(f"last event was {last_event_type}, not response.completed")
    yield completed_item


async def prewarm(  # noqa: PLR0913
    config: Config,
    *,
    instructions: str,
    input: MessageHistory,
    tools: list[Tool],
    prompt_cache_key: str,
) -> OpenAIWebsocketSession:
    session = WEBSOCKET_SESSIONS.setdefault(prompt_cache_key, OpenAIWebsocketSession())
    async with session.lock:
        await _prewarm_if_needed(
            session,
            config,
            instructions=instructions,
            input=input,
            tools=[get_tools_definition(tool) for tool in tools] + _cloud_tools(),
            prompt_cache_key=prompt_cache_key,
        )
        if not session.previous_response_id:
            # comment: streaming must only enter through a successful prewarm.
            raise MissingPrewarmResponseIDError(
                "OpenAI websocket prewarm did not return response id"
            )
    return session


def _completed_update(
    item: _CompletedResponse,
) -> tuple[MessageHistory, Any | None, list[Any]]:
    messages = [_to_message_item(output) for output in item.response_output]
    usage = item.event.response.usage.to_dict() if item.event.response.usage else None
    if messages and item.response_id:
        # comment: item["id"] is msg_/fc_/rs_; previous_response_id needs resp_.
        messages[-1]["response_id"] = item.response_id
    if messages and usage:
        # comment: empty responses have no assistant item to attach usage to.
        messages[-1]["usage"] = usage
    tool_calls = _tool_calls_from_response(item.event, item.response_output)
    return messages, item.response_id, tool_calls


async def _prepare_retry(session: OpenAIWebsocketSession, exc: Exception) -> None:
    await _close_session(session)
    if session.stream_retry_count >= WEBSOCKET_STREAM_RETRIES:
        raise exc

    session.stream_retry_count += 1
    logger.warning(
        "Retrying OpenAI websocket via prewarm gate (%s/%s): %s",
        session.stream_retry_count,
        WEBSOCKET_STREAM_RETRIES,
        exc,
    )
    await asyncio.sleep(min(0.2 * (2 ** (session.stream_retry_count - 1)), 5.0))


async def streaming_reply(  # noqa: C901
    config: Config,
    *,
    instructions: str,
    input: MessageHistory,
    tools: list[Tool],
    prompt_cache_key: str,
) -> AsyncIterator[StreamingReplyItem]:
    tools_payload = [get_tools_definition(tool) for tool in tools] + _cloud_tools()
    session = await prewarm(
        config,
        instructions=instructions,
        input=input,
        tools=tools,
        prompt_cache_key=prompt_cache_key,
    )
    tools_by_name = {_callable_name(tool): tool for tool in tools}
    replace_unavailable_uploads = uses_chatgpt_oauth(config)
    try:
        async with session.lock:
            # Continue inside the same turn while the model asks for tool calls.
            while True:
                tool_calls: list[Any] = []
                current_input = _current_input(
                    input,
                    session,
                    replace_unavailable_uploads=replace_unavailable_uploads,
                )
                payload = _get_payload(
                    config,
                    instructions=instructions,
                    input_items=current_input,
                    tools=tools_payload,
                    prompt_cache_key=prompt_cache_key,
                    previous_response_id=session.previous_response_id,
                )
                async for item in _read_response(session, payload):
                    if not isinstance(item, _CompletedResponse):
                        yield item
                        continue

                    messages, response_id, tool_calls = _completed_update(item)
                    input.extend(messages)
                    if not response_id:
                        raise MissingPrewarmResponseIDError(
                            "OpenAI websocket completed without response id"
                        )
                    session.previous_response_id = response_id
                    session.input_index = len(input)
                    yield item.event

                if not tool_calls:
                    session.stream_retry_count = 0
                    return

                for tool_call in tool_calls:
                    result = await _tool_result(tools_by_name, tool_call)
                    input.append(_to_message_item(result))
                    yield result
    except Exception as exc:
        if _is_client_error(exc):
            # comment: bad auth/payload should fail instead of replaying history.
            raise
        await _prepare_retry(session, exc)
        async for item in streaming_reply(
            config,
            instructions=instructions,
            input=input,
            tools=tools,
            prompt_cache_key=prompt_cache_key,
        ):
            yield item
