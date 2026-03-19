import inspect
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from enum import Enum
from typing import Any, TypeAlias

from openai import AsyncOpenAI
from openai.types.responses import (
    FunctionToolParam,
    ResponseFunctionToolCall,
    ResponseFunctionToolCallOutputItem,
    ResponsesServerEvent,
)

COMPACT_THRESHOLD = 210_000

Tool: TypeAlias = Callable[..., str] | Callable[..., Awaitable[str]]
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


def get_tools_definition(function: Callable[..., Any]) -> FunctionToolParam:
    sig = inspect.signature(function)
    docs = inspect.getdoc(function)
    if not docs:
        raise ValueError(f"Missing docstring for {function.__name__}")

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
                function.__name__,
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
        name=function.__name__,
        parameters=parameters,
        strict=True,
        description=description["function_docs"],
    )


def _item_dict(value: Any) -> dict[str, Any] | None:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    return value if isinstance(value, dict) else None


def _compacted_items(items: list[Any]) -> list[Any]:
    """Keep only the latest compacted history window.

    When the API returns a ``compaction`` item, it replaces all earlier context.
    So for the next request we can drop everything before the newest compaction
    item and send only that compacted item plus whatever came after it.
    """
    for index in range(len(items) - 1, -1, -1):
        item = _item_dict(items[index])
        if item and item.get("type") == "compaction":
            return items[index:]
    return items


def _parse_tool_arguments(raw_arguments: str) -> tuple[dict[str, Any], str | None]:
    try:
        value = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        return {}, f"Couldn't parse the arguments to the tool: {exc.msg}"
    if not isinstance(value, dict):
        return {}, "Tool arguments must decode to a JSON object."
    return value, None


def _tool_call_item(raw_item: dict[str, Any]) -> ResponseFunctionToolCall:
    return ResponseFunctionToolCall(
        type="function_call",
        id=raw_item.get("id"),
        call_id=str(raw_item.get("call_id", "")),
        name=str(raw_item.get("name", "")),
        arguments=str(raw_item.get("arguments", "")),
        status=raw_item.get("status"),
        namespace=raw_item.get("namespace"),
    )


def _response_tool_calls(response_output: list[Any]) -> list[ResponseFunctionToolCall]:
    tool_calls: list[ResponseFunctionToolCall] = []
    for item in response_output:
        raw_item = _item_dict(item)
        if raw_item and raw_item.get("type") == "function_call":
            tool_calls.append(_tool_call_item(raw_item))
    return tool_calls


async def _run_tool(function: Tool, kwargs: dict[str, Any]) -> str:
    result = function(**kwargs)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, str):
        raise TypeError(f"Tool {function.__name__} must return str")
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
    model: str,
    input: list[Any],
    tools: list[Tool],
) -> AsyncIterator[StreamingReplyItem]:
    client = AsyncOpenAI()
    tool_defs = [get_tools_definition(tool) for tool in tools]
    tools_by_name = {tool.__name__: tool for tool in tools}

    async def reply(current_input: list[Any]) -> AsyncIterator[StreamingReplyItem]:
        async with client.responses.stream(
            model=model,
            input=_compacted_items(current_input),
            tools=tool_defs,
            store=False,
            parallel_tool_calls=True,
            reasoning={"summary": "auto"},
            context_management=[
                {"type": "compaction", "compact_threshold": COMPACT_THRESHOLD}
            ],
        ) as stream:
            async for event in stream:
                yield event
            response = await stream.get_final_response()

        response_output = getattr(response, "output", [])
        current_input.extend(response_output)
        tool_calls = _response_tool_calls(response_output)
        if not tool_calls:
            return

        for tool_call in tool_calls:
            result = await _tool_result(tools_by_name, tool_call)
            current_input.append(result.to_dict())
            yield result

        async for item in reply(current_input):
            yield item

    try:
        async for item in reply(input):
            yield item
    finally:
        await client.close()
