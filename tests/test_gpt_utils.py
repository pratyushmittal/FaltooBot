from enum import Enum
from types import SimpleNamespace
from typing import Any, cast

import pytest
from openai.types.responses import (
    ResponseCompactionItem,
    ResponseFunctionCallArgumentsDeltaEvent,
    ResponseFunctionCallArgumentsDoneEvent,
    ResponseFunctionToolCall,
    ResponseFunctionToolCallOutputItem,
    ResponseInputParam,
    ResponseOutputItemAddedEvent,
    ResponseOutputItemDoneEvent,
    ResponseReasoningTextDeltaEvent,
    ResponseReasoningTextDoneEvent,
    ResponseTextDeltaEvent,
    ResponseTextDoneEvent,
)

from faltoobot import gpt_utils
from faltoobot.gpt_utils import get_streaming_reply, get_tools_definition


class Mode(str, Enum):
    FAST = "fast"
    SAFE = "safe"


class FakeItem:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def to_dict(self) -> dict[str, Any]:
        return self.payload


class FakeResponse:
    def __init__(self, output: list[dict[str, Any]]) -> None:
        self.output = [FakeItem(item) for item in output]


class FakeStreamManager:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response

    async def __aenter__(self) -> "FakeStreamManager":
        return self

    async def __aexit__(self, exc_type: object, exc: object, exc_tb: object) -> None:
        return None

    def __aiter__(self) -> "FakeStreamManager":
        self._events = iter(self.response["events"])
        return self

    async def __anext__(self) -> object:
        try:
            return next(self._events)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    async def get_final_response(self) -> FakeResponse:
        return FakeResponse(self.response["output"])


class FakeResponses:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []
        self.index = 0

    def stream(self, **kwargs: Any) -> FakeStreamManager:
        self.calls.append(kwargs)
        response = self.responses[self.index]
        self.index += 1
        return FakeStreamManager(response)


class FakeClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = FakeResponses(responses)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def sample_tool(name: str, count: int, mode: Mode) -> str:
    """Run a sample tool.

    Args:
        - name: User name.
        - count: Retry count.
        - mode: Execution mode.
    """
    return f"{name}-{count}-{mode.value}"


def greet(name: str) -> str:
    """Greet a user.

    Args:
        - name: User name.
    """
    return f"hello {name}"


def test_get_tools_definition() -> None:
    tool = get_tools_definition(sample_tool)
    parameters = cast(dict[str, Any], tool["parameters"])
    properties = cast(dict[str, Any], parameters["properties"])

    assert tool["type"] == "function"
    assert tool["name"] == "sample_tool"
    assert tool["strict"] is True
    assert tool["description"] == "Run a sample tool."
    assert parameters["required"] == ["name", "count", "mode"]
    assert properties["name"] == {
        "type": "string",
        "description": "User name.",
    }
    assert properties["count"] == {
        "type": "integer",
        "description": "Retry count.",
    }
    assert properties["mode"] == {
        "type": "string",
        "description": "Execution mode.",
        "enum": ["fast", "safe"],
    }


@pytest.mark.anyio
async def test_get_streaming_reply_recurses_for_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(
        [
            {
                "events": [
                    ResponseReasoningTextDeltaEvent(
                        type="response.reasoning_text.delta",
                        content_index=0,
                        delta="plan",
                        item_id="rs_1",
                        output_index=0,
                        sequence_number=1,
                    ),
                    ResponseReasoningTextDoneEvent(
                        type="response.reasoning_text.done",
                        content_index=0,
                        item_id="rs_1",
                        output_index=0,
                        sequence_number=2,
                        text="plan",
                    ),
                    ResponseTextDeltaEvent(
                        type="response.output_text.delta",
                        content_index=0,
                        delta="Let me check. ",
                        item_id="msg_1",
                        output_index=0,
                        sequence_number=3,
                        logprobs=[],
                    ),
                    ResponseTextDoneEvent(
                        type="response.output_text.done",
                        content_index=0,
                        item_id="msg_1",
                        output_index=0,
                        sequence_number=4,
                        text="Let me check. ",
                        logprobs=[],
                    ),
                    ResponseOutputItemAddedEvent(
                        type="response.output_item.added",
                        output_index=1,
                        sequence_number=5,
                        item=ResponseFunctionToolCall(
                            type="function_call",
                            id="fc_1",
                            call_id="call_1",
                            name="greet",
                            arguments="",
                        ),
                    ),
                    ResponseFunctionCallArgumentsDeltaEvent(
                        type="response.function_call_arguments.delta",
                        item_id="fc_1",
                        output_index=1,
                        sequence_number=6,
                        delta='{"name":"Faltoo',
                    ),
                    ResponseFunctionCallArgumentsDoneEvent(
                        type="response.function_call_arguments.done",
                        item_id="fc_1",
                        output_index=1,
                        sequence_number=7,
                        name="greet",
                        arguments='{"name":"Faltoobot"}',
                    ),
                    ResponseOutputItemDoneEvent(
                        type="response.output_item.done",
                        output_index=1,
                        sequence_number=8,
                        item=ResponseFunctionToolCall(
                            type="function_call",
                            id="fc_1",
                            call_id="call_1",
                            name="greet",
                            arguments='{"name":"Faltoobot"}',
                        ),
                    ),
                ],
                "output": [
                    {
                        "type": "function_call",
                        "id": "fc_1",
                        "call_id": "call_1",
                        "name": "greet",
                        "arguments": '{"name":"Faltoobot"}',
                    }
                ],
            },
            {
                "events": [
                    ResponseTextDeltaEvent(
                        type="response.output_text.delta",
                        content_index=0,
                        delta="Done.",
                        item_id="msg_2",
                        output_index=0,
                        sequence_number=1,
                        logprobs=[],
                    ),
                    ResponseTextDoneEvent(
                        type="response.output_text.done",
                        content_index=0,
                        item_id="msg_2",
                        output_index=0,
                        sequence_number=2,
                        text="Done.",
                        logprobs=[],
                    ),
                ],
                "output": [],
            },
        ]
    )
    monkeypatch.setattr(gpt_utils, "AsyncOpenAI", lambda api_key=None: client)
    monkeypatch.setattr(
        gpt_utils,
        "build_config",
        lambda: SimpleNamespace(
            openai_model="gpt-5-mini",
            openai_thinking="low",
            openai_fast=False,
        ),
    )

    items = [
        item
        async for item in get_streaming_reply(
            instructions="system prompt",
            input=[{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
            tools=[greet],
            api_key="test-key",
        )
    ]

    assert [getattr(item, "type", "response") for item in items] == [
        "response.reasoning_text.delta",
        "response.reasoning_text.done",
        "response.output_text.delta",
        "response.output_text.done",
        "response.output_item.added",
        "response.function_call_arguments.delta",
        "response.function_call_arguments.done",
        "response.output_item.done",
        "response",
        "function_call_output",
        "response.output_text.delta",
        "response.output_text.done",
        "response",
    ]
    tool_output = cast(ResponseFunctionToolCallOutputItem, items[9])
    assert tool_output.output == "hello Faltoobot"
    assert client.responses.calls[0]["context_management"] == [
        {"type": "compaction", "compact_threshold": 210_000}
    ]
    assert client.responses.calls[1]["input"][-1] == {
        "id": "fco_call_1",
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "hello Faltoobot",
        "status": "completed",
    }
    assert client.closed is True


@pytest.mark.anyio
async def test_get_streaming_reply_yields_all_stream_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(
        [
            {
                "events": [
                    ResponseOutputItemDoneEvent(
                        type="response.output_item.done",
                        output_index=0,
                        sequence_number=1,
                        item=ResponseCompactionItem(
                            type="compaction",
                            id="cmp_1",
                            encrypted_content="secret",
                        ),
                    )
                ],
                "output": [
                    {
                        "type": "compaction",
                        "id": "cmp_1",
                        "encrypted_content": "secret",
                    }
                ],
            }
        ]
    )
    monkeypatch.setattr(gpt_utils, "AsyncOpenAI", lambda api_key=None: client)
    monkeypatch.setattr(
        gpt_utils,
        "build_config",
        lambda: SimpleNamespace(
            openai_model="gpt-5-mini",
            openai_thinking="low",
            openai_fast=False,
        ),
    )

    items = [
        item
        async for item in get_streaming_reply(
            instructions="system prompt",
            input=[{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
            tools=[],
            api_key="test-key",
        )
    ]

    assert [getattr(item, "type", "response") for item in items] == [
        "response.output_item.done",
        "response",
    ]
    assert isinstance(items[1], FakeResponse)
    assert client.closed is True


@pytest.mark.anyio
async def test_get_streaming_reply_trims_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient([{"events": [], "output": []}])
    monkeypatch.setattr(gpt_utils, "AsyncOpenAI", lambda api_key=None: client)
    monkeypatch.setattr(
        gpt_utils,
        "build_config",
        lambda: SimpleNamespace(
            openai_model="gpt-5-mini",
            openai_thinking="low",
            openai_fast=False,
        ),
    )

    items = cast(
        ResponseInputParam,
        [
            {"type": "message", "role": "user", "content": "old"},
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "greet",
                "arguments": '{"name":"Faltoobot"}',
                "parsed_arguments": {"name": "Faltoobot"},
            },
            {"type": "compaction", "id": "cmp_1", "encrypted_content": "secret"},
            {"type": "message", "role": "user", "content": "hi"},
        ],
    )

    [item async for item in get_streaming_reply("system prompt", items, [], "test-key")]

    assert client.responses.calls[0]["input"] == [
        {"type": "compaction", "id": "cmp_1", "encrypted_content": "secret"},
        {"type": "message", "role": "user", "content": "hi"},
    ]
