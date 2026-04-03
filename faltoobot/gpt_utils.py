import asyncio
import inspect
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from enum import Enum
from typing import Any, TypeAlias, cast

from openai import AsyncOpenAI, omit
from openai.types.responses import (
    FunctionToolParam,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseFunctionToolCallOutputItem,
    ResponsesServerEvent,
)

from faltoobot.config import Config, build_config
from faltoobot.openai_auth import get_openai_client_options

COMPACT_THRESHOLD = 210_000

Tool: TypeAlias = Callable[..., str] | Callable[..., Awaitable[str]]
MessageItem: TypeAlias = dict[str, Any]
MessageHistory: TypeAlias = list[MessageItem]
StreamingReplyItem: TypeAlias = (
    ResponsesServerEvent | ResponseFunctionToolCallOutputItem
)


def _parse_docs(docs: str) -> dict[str, Any]:
    function_description, args_description = docs.strip().split("\nArgs:\n")
    function_description = function_description.strip()

    args_lines = args_description.split("\n    - ")
    arguments = {}
    for arg_line in args_lines:
        name, description = arg_line.split(": ", maxsplit=1)
        description = "\n".join(line.strip() for line in description.splitlines())
        arguments[name.strip(" -")] = description.strip()

    return {"function_docs": function_description, "arguments": arguments}


def _callable_name(function: Callable[..., Any]) -> str:
    return getattr(function, "__name__", type(function).__name__)


def get_openai_client(config: Config) -> AsyncOpenAI:
    api_key, base_url, default_headers = get_openai_client_options(config)
    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    if default_headers:
        kwargs["default_headers"] = default_headers
    return AsyncOpenAI(**kwargs)


def get_tools_definition(function: Callable[..., Any]) -> FunctionToolParam:
    sig = inspect.signature(function)
    docs = inspect.getdoc(function)
    if not docs:
        raise ValueError(f"Missing docstring for {_callable_name(function)}")

    description = _parse_docs(docs)
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }

    for param_name, param in sig.parameters.items():
        if param_name.startswith("_"):
            continue

        other_param_properties: dict[str, Any] = {}
        if param.annotation is int:
            param_type = "integer"
        elif param.annotation is str:
            param_type = "string"
        elif inspect.isclass(param.annotation) and issubclass(param.annotation, Enum):
            param_type = "string"
            other_param_properties["enum"] = [value.value for value in param.annotation]
        else:
            raise ValueError("undefined type", param.annotation)

        if param_name not in description["arguments"]:
            raise ValueError(
                "Documentation not provided in",
                param_name,
                _callable_name(function),
            )

        if param.default != inspect._empty:
            raise ValueError(
                "defaults not implemented by us. underscore the param to skip it.",
                function,
                param,
            )

        parameters["required"].append(param_name)
        parameters["properties"][param_name] = {
            "type": param_type,
            "description": description["arguments"][param_name],
            **other_param_properties,
        }

    return FunctionToolParam(
        type="function",
        name=_callable_name(function),
        parameters=parameters,
        strict=True,
        description=description["function_docs"],
    )


def _to_message_item(value: Any) -> MessageItem:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, dict):
        raise TypeError(f"Expected dict-like item, got {type(value).__name__}")
    return value


def trim_input(items: MessageHistory) -> MessageHistory:
    # Keep only the latest compacted history window.
    for index in range(len(items) - 1, -1, -1):
        if items[index].get("type") == "compaction":
            items = items[index:]
            break

    # Strip SDK-only fields before replaying saved items back to the API.
    items = [
        {
            key: value
            for key, value in item.items()
            if key not in {"parsed_arguments", "usage"}
        }
        for item in items
    ]
    return items


def _parse_tool_arguments(raw_arguments: str) -> tuple[dict[str, Any], str | None]:
    try:
        value = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        return {}, f"Couldn't parse the arguments to the tool: {exc.msg}"
    if not isinstance(value, dict):
        return {}, "Tool arguments must decode to a JSON object."
    return value, None


def _response_tool_calls(
    event: ResponseCompletedEvent,
) -> list[ResponseFunctionToolCall]:
    tool_calls: list[ResponseFunctionToolCall] = []
    for item in event.response.output:
        if item.type == "function_call":
            item = cast(ResponseFunctionToolCall, item)
            tool_calls.append(item)
    return tool_calls


async def _run_tool(function: Tool, kwargs: dict[str, Any]) -> str:
    if inspect.iscoroutinefunction(function):
        result = await function(**kwargs)
    else:
        # comment: sync tools can block the Textual event loop, so run them in a worker thread.
        result = await asyncio.to_thread(function, **kwargs)
        if inspect.isawaitable(result):
            result = await result
    if not isinstance(result, str):
        raise TypeError(f"Tool {_callable_name(function)} must return str")
    return result


async def _tool_result(
    tools_by_name: dict[str, Tool],
    tool_call: ResponseFunctionToolCall,
) -> ResponseFunctionToolCallOutputItem:
    arguments, error = _parse_tool_arguments(tool_call.arguments)
    if error:
        output = error
    elif tool_call.name not in tools_by_name:
        output = f"Function name error - unknown name: {tool_call.name}"
    else:
        try:
            output = await _run_tool(tools_by_name[tool_call.name], arguments)
        except TypeError as exc:
            output = f"error: {exc}"
        except Exception as exc:  # comment: tool failures should go back to the model.
            output = f"{type(exc).__name__}: {exc}"

    return ResponseFunctionToolCallOutputItem(
        id=f"fco_{tool_call.call_id}",
        type="function_call_output",
        call_id=tool_call.call_id,
        output=output,
        status="completed",
    )


async def get_streaming_reply(
    instructions: str,
    input: MessageHistory,
    tools: list[Tool],
    prompt_cache_key: str | None = None,
) -> AsyncIterator[StreamingReplyItem]:
    config = build_config()
    client = get_openai_client(config)
    tool_defs = [get_tools_definition(tool) for tool in tools]
    tools_by_name = {_callable_name(tool): tool for tool in tools}

    cloud_tools = [
        {
            "type": "web_search",
            "user_location": {
                "type": "approximate",
                "country": "IN",
                "city": "Lucknow",
                "region": "Lucknow",
            },
        }
    ]

    async def reply(
        current_input: MessageHistory,
    ) -> AsyncIterator[StreamingReplyItem]:
        async with client.responses.stream(
            model=config.openai_model,
            input=cast(Any, trim_input(current_input)),
            tools=tool_defs + cloud_tools,  # type: ignore
            store=False,
            parallel_tool_calls=True,
            instructions=instructions,
            reasoning={"summary": "auto", "effort": config.openai_thinking},  # type: ignore
            include=["reasoning.encrypted_content", "web_search_call.action.sources"],
            context_management=[
                {"type": "compaction", "compact_threshold": COMPACT_THRESHOLD}
            ],
            prompt_cache_key=prompt_cache_key or omit,
            service_tier="priority" if config.openai_fast else omit,
        ) as stream:
            async for event in stream:
                if event.type == "response.completed":
                    # Normalize output items to plain dicts before sharing and persisting
                    # history, then attach usage to the last item from this response.
                    event = cast(ResponseCompletedEvent, event)
                    dict_response = event.response.to_dict()
                    current_input.extend(dict_response["output"])  # type: ignore
                    current_input[-1]["usage"] = dict_response["usage"]
                yield event

        # https://developers.openai.com/api/reference/resources/responses/streaming-events#response.completed
        # the last event is always `response.completed`
        # it always contains full response in event.response
        # including usage
        if event.type != "response.completed":
            raise ValueError("last event was not response.completed")
        event = cast(ResponseCompletedEvent, event)

        tool_calls = _response_tool_calls(event)
        if not tool_calls:
            return

        for tool_call in tool_calls:
            result = await _tool_result(tools_by_name, tool_call)
            current_input.append(_to_message_item(result))
            yield result

        async for item in reply(current_input):
            yield item

    try:
        async for item in reply(input):
            yield item
    finally:
        await client.close()
