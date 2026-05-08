import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, cast

from openai._models import construct_type_unchecked
from openai.types.responses import (
    FunctionToolParam,
    ResponseCompletedEvent,
    ResponseOutputItem,
    ResponsesServerEvent,
)
from websockets.asyncio.client import connect as websocket_connect

from faltoobot.config import Config
from faltoobot.openai_auth import get_openai_client_options, uses_chatgpt_oauth
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

RESPONSES_WEBSOCKET_URL = "wss://api.openai.com/v1/responses"


async def _auth_headers(
    api_key: str | Callable[[], Awaitable[str]],
    default_headers: dict[str, str] | None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    token = api_key if isinstance(api_key, str) else await api_key()
    return {
        **(default_headers or {}),
        **(extra_headers or {}),
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


def _raise_for_response_error(event: ResponsesServerEvent) -> None:
    if event.type != "error":
        return
    error = getattr(event, "error", None)
    if isinstance(error, dict):
        code = error.get("code") or error.get("type") or "error"
        message = error.get("message") or error
    else:
        code = (
            getattr(error, "code", None)
            or getattr(error, "type", None)
            or getattr(event, "code", None)
            or "error"
        )
        message = (
            getattr(error, "message", None)
            or getattr(event, "message", None)
            or error
            or "Unknown websocket error"
        )
    exception = f"OpenAI websocket {code}: {message}"
    if code == "previous_response_not_found":
        raise PreviousResponseNotFoundError(exception)
    raise RuntimeError(exception)


def _create_payload(  # noqa: PLR0913
    config: Config,
    *,
    instructions: str,
    input_items: MessageHistory,
    tools: list[FunctionToolParam | dict[str, Any]],
    prompt_cache_key: str | None,
    previous_response_id: str | None,
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
    if prompt_cache_key:
        payload["prompt_cache_key"] = prompt_cache_key
    if previous_response_id:
        payload["previous_response_id"] = previous_response_id
    if config.openai_fast:
        payload["service_tier"] = "priority"
    return payload


async def streaming_reply(  # noqa: C901, PLR0913
    config: Config,
    *,
    instructions: str,
    input: MessageHistory,
    tools: list[Tool],
    prompt_cache_key: str | None,
) -> AsyncIterator[StreamingReplyItem]:
    tool_defs = [get_tools_definition(tool) for tool in tools]
    tools_by_name = {_callable_name(tool): tool for tool in tools}
    replace_unavailable_uploads = uses_chatgpt_oauth(config)
    previous_response_id: str | None = None
    current_input = trim_input(
        input, replace_unavailable_uploads=replace_unavailable_uploads
    )
    retried_missing_previous_response = False

    api_key, base_url, default_headers = get_openai_client_options(config)
    async with websocket_connect(
        _websocket_url(base_url),
        additional_headers=await _auth_headers(
            api_key,
            default_headers,
            _request_extra_headers(config, prompt_cache_key),
        ),
    ) as ws:
        while True:
            response_output: list[ResponseOutputItem] = []
            await ws.send(
                json.dumps(
                    _create_payload(
                        config,
                        instructions=instructions,
                        input_items=current_input,
                        tools=tool_defs + _cloud_tools(),
                        prompt_cache_key=prompt_cache_key,
                        previous_response_id=previous_response_id,
                    )
                )
            )

            async for raw in ws:
                event = _parse_websocket_event(raw)
                try:
                    _raise_for_response_error(event)
                except PreviousResponseNotFoundError:
                    if (
                        retried_missing_previous_response
                        or previous_response_id is None
                    ):
                        # comment: repeated/malformed retry should fail instead of looping forever.
                        raise
                    # comment: cached response expired; replay full trimmed history once.
                    current_input = trim_input(
                        input, replace_unavailable_uploads=replace_unavailable_uploads
                    )
                    retried_missing_previous_response = True
                    previous_response_id = None
                    break
                response_id = _remember_response_event(event, response_output)
                if event.type != "response.completed":
                    yield event
                    continue

                completed = cast(ResponseCompletedEvent, event)
                input.extend(_to_message_item(item) for item in response_output)
                # comment: item["id"] is msg_/fc_/rs_; previous_response_id needs resp_.
                if response_output and response_id:
                    input[-1]["response_id"] = response_id
                # comment: empty responses have no assistant item to attach usage to.
                if response_output and completed.response.usage:
                    input[-1]["usage"] = completed.response.usage.to_dict()
                previous_response_id = response_id
                yield event

                tool_calls = _tool_calls_from_response(event, response_output)
                if not tool_calls:
                    return

                current_input = []
                for tool_call in tool_calls:
                    result = await _tool_result(tools_by_name, tool_call)
                    result_item = _to_message_item(result)
                    input.append(result_item)
                    current_input.append(result_item)
                    yield result

                if previous_response_id is None:
                    # comment: without previous_response_id, replay full history for tool context.
                    current_input = trim_input(
                        input, replace_unavailable_uploads=replace_unavailable_uploads
                    )
                break
            else:
                raise ValueError("websocket closed before response.completed")
