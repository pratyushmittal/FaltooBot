import asyncio
import base64
import json
import ssl
import threading
import time
from enum import Enum
from io import BytesIO
from types import SimpleNamespace
from typing import Any, cast

import pytest
from openai import omit
from PIL import Image
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosedError, InvalidStatus
from websockets.frames import Close
from websockets.http11 import Response
from openai.types.responses import (
    ResponseCompactionItem,
    ResponseFunctionCallArgumentsDeltaEvent,
    ResponseFunctionCallArgumentsDoneEvent,
    ResponseFunctionToolCall,
    ResponseFunctionToolCallOutputItem,
    ResponseInputImage,
    ResponseOutputItemAddedEvent,
    ResponseOutputItemDoneEvent,
    ResponseReasoningTextDeltaEvent,
    ResponseReasoningTextDoneEvent,
    ResponseTextDeltaEvent,
    ResponseTextDoneEvent,
)

from faltoobot import gpt_utils, sessions
from faltoobot.config import Config
from faltoobot.gpt_utils import (
    MessageHistory,
    get_streaming_reply as get_api_streaming_reply,
    get_tools_definition,
)

RESPONSIVE_TOOL_MAX_SECONDS = 0.15


def _api_config() -> Config:
    return cast(
        Config,
        SimpleNamespace(
            openai_model="gpt-5-mini",
            openai_api_key="test-key",
            openai_oauth="",
            openai_thinking="low",
            openai_fast=False,
        ),
    )


async def get_streaming_reply(
    instructions: str,
    input: MessageHistory,
    tools: list[Any],
    prompt_cache_key: str | None = None,
):
    async for item in get_api_streaming_reply(
        _api_config(),
        instructions=instructions,
        input=input,
        tools=tools,
        prompt_cache_key=prompt_cache_key,
    ):
        yield item


def test_trim_input_drops_image_generation_calls() -> None:
    history: MessageHistory = [
        {"role": "user", "content": "draw"},
        {"type": "image_generation_call", "id": "ig_1", "status": "failed"},
        {"role": "user", "content": "thanks"},
    ]

    assert gpt_utils.trim_input(history) == [
        {"role": "user", "content": "draw"},
        {"role": "user", "content": "thanks"},
    ]


def test_get_openai_client_uses_codex_retry_limits() -> None:
    client = gpt_utils.get_openai_client(_api_config())

    timeout = cast(Any, client.timeout)

    assert client.max_retries == gpt_utils.REQUEST_MAX_RETRIES
    assert timeout.read == gpt_utils.STREAM_IDLE_TIMEOUT_SECONDS


class Mode(str, Enum):
    FAST = "fast"
    SAFE = "safe"


class FakeItem:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.type = str(payload.get("type") or "")
        for key, value in payload.items():
            setattr(self, key, value)

    def __getitem__(self, key: str) -> Any:
        return self.payload[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.payload[key] = value
        setattr(self, key, value)

    def to_dict(self) -> dict[str, Any]:
        return self.payload


class FakeResponse:
    def __init__(
        self,
        output: list[dict[str, Any]] | None,
        usage: dict[str, Any] | None = None,
    ) -> None:
        self.output = None if output is None else [FakeItem(item) for item in output]
        self.usage = usage

    def to_dict(self) -> dict[str, Any]:
        return {
            "output": None
            if self.output is None
            else [item.to_dict() for item in self.output],
            "usage": self.usage,
        }


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

    async def create(self, **kwargs: Any) -> FakeStreamManager:
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


class FakeCompletedEvent:
    def __init__(self, output: list[dict[str, Any]] | None) -> None:
        self.type = "response.completed"
        self.response = FakeResponse(output)


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
                    FakeCompletedEvent(
                        [
                            {
                                "type": "function_call",
                                "id": "fc_1",
                                "call_id": "call_1",
                                "name": "greet",
                                "arguments": '{"name":"Faltoobot"}',
                            }
                        ]
                    ),
                ],
                "output": [],
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
                    FakeCompletedEvent([]),
                ],
                "output": [],
            },
        ]
    )
    monkeypatch.setattr(gpt_utils, "get_openai_client", lambda config: client)

    items = [
        item
        async for item in get_streaming_reply(
            instructions="system prompt",
            input=[{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
            tools=[greet],
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
        "response.completed",
        "function_call_output",
        "response.output_text.delta",
        "response.output_text.done",
        "response.completed",
    ]
    tool_output = cast(ResponseFunctionToolCallOutputItem, items[9])
    assert tool_output.output == "hello Faltoobot"
    assert client.responses.calls[0]["context_management"] == [
        {"type": "compaction", "compact_threshold": 200_000}
    ]
    assert client.responses.calls[0]["reasoning"] == {
        "summary": "concise",
        "effort": "low",
    }
    assert client.responses.calls[0]["tools"][-1] == {"type": "image_generation"}
    assert client.responses.calls[0]["prompt_cache_key"] == omit
    assert client.responses.calls[0]["extra_headers"] is None
    assert client.responses.calls[1]["input"][-1] == {
        "id": "fco_call_1",
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "hello Faltoobot",
        "status": "completed",
    }


@pytest.mark.anyio
async def test_get_streaming_reply_uses_output_item_done_when_completed_output_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(
        [
            {
                "events": [
                    SimpleNamespace(
                        type="response.output_item.done",
                        item=FakeItem(
                            {
                                "type": "function_call",
                                "id": "fc_1",
                                "call_id": "call_1",
                                "name": "greet",
                                "arguments": '{"name":"Faltoobot"}',
                            }
                        ),
                    ),
                    FakeCompletedEvent([]),
                ],
                "output": [],
            },
            {
                "events": [
                    SimpleNamespace(
                        type="response.output_item.done",
                        item=FakeItem(
                            {
                                "type": "message",
                                "id": "msg_2",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "Done."}],
                            }
                        ),
                    ),
                    FakeCompletedEvent([]),
                ],
                "output": [],
            },
        ]
    )
    monkeypatch.setattr(gpt_utils, "get_openai_client", lambda config: client)

    history: MessageHistory = [
        {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}
    ]

    items = [
        item
        async for item in get_streaming_reply(
            instructions="system prompt",
            input=history,
            tools=[greet],
        )
    ]

    assert [getattr(item, "type", "response") for item in items] == [
        "response.output_item.done",
        "response.completed",
        "function_call_output",
        "response.output_item.done",
        "response.completed",
    ]
    assert history == [
        {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "greet",
            "arguments": '{"name":"Faltoobot"}',
        },
        {
            "id": "fco_call_1",
            "type": "function_call_output",
            "call_id": "call_1",
            "output": "hello Faltoobot",
            "status": "completed",
        },
        {
            "type": "message",
            "id": "msg_2",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Done."}],
        },
    ]
    assert cast(Any, items[1]).response.codex_output[0].to_dict() == {
        "type": "function_call",
        "id": "fc_1",
        "call_id": "call_1",
        "name": "greet",
        "arguments": '{"name":"Faltoobot"}',
    }
    assert cast(Any, items[4]).response.codex_output[0].to_dict() == {
        "type": "message",
        "id": "msg_2",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "Done."}],
    }
    assert client.closed is True


@pytest.mark.anyio
async def test_get_streaming_reply_accepts_completed_output_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(
        [
            {
                "events": [
                    SimpleNamespace(
                        type="response.output_item.done",
                        item=FakeItem(
                            {
                                "type": "message",
                                "id": "msg_1",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "Done."}],
                            }
                        ),
                    ),
                    FakeCompletedEvent(None),
                ],
                "output": [],
            }
        ]
    )
    monkeypatch.setattr(gpt_utils, "get_openai_client", lambda config: client)

    history: MessageHistory = [
        {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}
    ]

    items = [
        item
        async for item in get_streaming_reply(
            instructions="system prompt",
            input=history,
            tools=[],
        )
    ]

    assert getattr(items[-1], "type", "") == "response.completed"
    assert history[-1] == {
        "type": "message",
        "id": "msg_1",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "Done."}],
    }


@pytest.mark.anyio
async def test_get_streaming_reply_updates_history_before_completed_yield(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(
        [
            {
                "events": [
                    SimpleNamespace(
                        type="response.output_item.done",
                        item=FakeItem(
                            {
                                "type": "message",
                                "id": "msg_1",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "Hi."}],
                            }
                        ),
                    ),
                    FakeCompletedEvent([]),
                ],
                "output": [],
            }
        ]
    )
    monkeypatch.setattr(gpt_utils, "get_openai_client", lambda config: client)

    history: MessageHistory = [
        {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}
    ]
    seen_completed = False

    async for item in get_streaming_reply(
        instructions="system prompt",
        input=history,
        tools=[],
    ):
        if item.type == "response.completed":
            seen_completed = True
            assert history[-1] == {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Hi."}],
            }

    assert seen_completed is True


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
                    ),
                    FakeCompletedEvent(
                        [
                            {
                                "type": "compaction",
                                "id": "cmp_1",
                                "encrypted_content": "secret",
                            }
                        ]
                    ),
                ],
                "output": [],
            }
        ]
    )
    monkeypatch.setattr(gpt_utils, "get_openai_client", lambda config: client)

    items = [
        item
        async for item in get_streaming_reply(
            instructions="system prompt",
            input=[{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
            tools=[],
        )
    ]

    assert [getattr(item, "type", "response") for item in items] == [
        "response.output_item.done",
        "response.completed",
    ]
    assert client.closed is True


@pytest.mark.anyio
async def test_get_streaming_reply_trims_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(
        [
            {
                "events": [FakeCompletedEvent([])],
                "output": [],
            }
        ]
    )
    monkeypatch.setattr(gpt_utils, "get_openai_client", lambda config: client)

    items = cast(
        MessageHistory,
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
            {
                "type": "message",
                "role": "assistant",
                "content": "hi",
                "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            },
        ],
    )

    [item async for item in get_streaming_reply("system prompt", items, [])]

    assert client.responses.calls[0]["input"] == [
        {"type": "message", "role": "user", "content": "old"},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "greet",
            "arguments": '{"name":"Faltoobot"}',
        },
        {"type": "compaction", "id": "cmp_1", "encrypted_content": "secret"},
        {"type": "message", "role": "assistant", "content": "hi"},
    ]


@pytest.mark.anyio
async def test_get_streaming_reply_keeps_standalone_compaction_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(
        [
            {
                "events": [FakeCompletedEvent([])],
                "output": [],
            }
        ]
    )
    monkeypatch.setattr(gpt_utils, "get_openai_client", lambda config: client)

    items = cast(
        MessageHistory,
        [
            {"type": "message", "role": "user", "content": "retained"},
            {
                "type": "compaction",
                "id": "cmp_1",
                "encrypted_content": "secret",
                gpt_utils.STANDALONE_COMPACTION_KEY: True,
            },
            {
                "type": "message",
                "role": "assistant",
                "content": "hi",
                "usage": {"total_tokens": 3},
            },
        ],
    )

    [item async for item in get_streaming_reply("system prompt", items, [])]

    assert client.responses.calls[0]["input"] == [
        {"type": "message", "role": "user", "content": "retained"},
        {"type": "compaction", "id": "cmp_1", "encrypted_content": "secret"},
        {"type": "message", "role": "assistant", "content": "hi"},
    ]


@pytest.mark.anyio
async def test_run_tool_keeps_event_loop_responsive_for_sync_tools() -> None:
    started = threading.Event()

    def slow_tool() -> str:
        started.set()
        time.sleep(0.2)
        return "done"

    started_at = time.perf_counter()
    task = asyncio.create_task(gpt_utils._run_tool(slow_tool, {}))
    assert await asyncio.wait_for(asyncio.to_thread(started.wait, 1.0), timeout=1.2)
    await asyncio.sleep(0)
    assert time.perf_counter() - started_at < RESPONSIVE_TOOL_MAX_SECONDS
    assert await task == "done"


@pytest.mark.anyio
async def test_get_streaming_reply_adds_codex_session_headers_for_oauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(
        [
            {
                "events": [
                    FakeCompletedEvent([]),
                ],
                "output": [],
            }
        ]
    )
    monkeypatch.setattr(gpt_utils, "get_openai_client", lambda config: client)
    monkeypatch.setattr(gpt_utils, "uses_chatgpt_oauth", lambda config: True)

    _items = [
        item
        async for item in get_streaming_reply(
            instructions="system prompt",
            input=[{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
            tools=[],
            prompt_cache_key="session-123",
        )
    ]

    assert client.responses.calls[0]["prompt_cache_key"] == "session-123"
    assert client.responses.calls[0]["extra_headers"] == {
        "session-id": "session-123",
        "thread-id": "session-123",
        "x-client-request-id": "session-123",
    }
    assert client.closed is True


@pytest.mark.anyio
async def test_get_streaming_reply_replaces_unavailable_uploaded_files_for_oauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(
        [
            {
                "events": [
                    FakeCompletedEvent([]),
                ],
                "output": [],
            }
        ]
    )
    monkeypatch.setattr(gpt_utils, "get_openai_client", lambda config: client)
    monkeypatch.setattr(gpt_utils, "uses_chatgpt_oauth", lambda config: True)

    history: MessageHistory = [
        {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": "look"},
                {"type": "input_image", "file_id": "file_old", "detail": "auto"},
            ],
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": [
                {"type": "input_image", "file_id": "file_tool", "detail": "auto"},
                {"type": "input_file", "file_id": "file_doc"},
            ],
        },
    ]

    _items = [
        item
        async for item in get_streaming_reply(
            instructions="system prompt",
            input=history,
            tools=[],
        )
    ]

    assert client.responses.calls[0]["input"] == [
        {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": "look"},
                {"type": "input_text", "text": "[image-not-available-now]"},
            ],
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": [
                {"type": "input_text", "text": "[image-not-available-now]"},
                {"type": "input_text", "text": "[file-not-available-now]"},
            ],
        },
    ]
    assert client.closed is True


@pytest.mark.anyio
async def test_tool_result_keeps_structured_image_output() -> None:
    async def load_image(image_path: str) -> list[ResponseInputImage]:
        """Load image files in supported image formats: jpeg, png, gif, or webp. Useful for seeing screenshots and creatives.

        Args:
            - image_path: relative or absolute path of the image
        """
        return [
            ResponseInputImage(
                type="input_image",
                image_url="data:image/png;base64,abc",
                detail="auto",
            )
        ]

    result = await gpt_utils._tool_result(
        cast(Any, {"load_image": load_image}),
        {
            "type": "function_call",
            "name": "load_image",
            "arguments": json.dumps({"image_path": "cat.png"}),
            "call_id": "call_1",
        },
    )

    assert result.type == "function_call_output"
    assert isinstance(result.output, list)
    assert len(result.output) == 1
    image = result.output[0]
    assert isinstance(image, ResponseInputImage)
    assert image.type == "input_image"
    assert image.image_url == "data:image/png;base64,abc"
    assert image.detail == "auto"


@pytest.mark.anyio
async def test_tool_result_renders_load_image_errors() -> None:
    async def load_image(image_path: str) -> list[ResponseInputImage]:
        """Load image files in supported image formats: jpeg, png, gif, or webp. Useful for seeing screenshots and creatives.

        Args:
            - image_path: relative or absolute path of the image
        """
        raise ValueError(
            "Unsupported image format for OpenAI: cat.avif. "
            "Supported formats: jpeg, png, gif, webp."
        )

    result = await gpt_utils._tool_result(
        cast(Any, {"load_image": load_image}),
        {
            "type": "function_call",
            "name": "load_image",
            "arguments": json.dumps({"image_path": "cat.avif"}),
            "call_id": "call_1",
        },
    )

    assert result.type == "function_call_output"
    assert result.output == (
        "ValueError: Unsupported image format for OpenAI: cat.avif. "
        "Supported formats: jpeg, png, gif, webp."
    )


class FakeWebSocket:
    def __init__(
        self,
        responses: list[list[dict[str, Any]] | Exception],
        *,
        connect_error: Exception | None = None,
    ) -> None:
        self.responses = responses
        self.connect_error = connect_error
        self.sent: list[dict[str, Any]] = []
        self.index = 0
        self.pending: list[str] | Exception = []

    def __await__(self) -> Any:
        async def connect() -> "FakeWebSocket":
            if self.connect_error is not None:
                raise self.connect_error
            return self

        return connect().__await__()

    async def close(self) -> None:
        return None

    async def __aenter__(self) -> "FakeWebSocket":
        return self

    async def __aexit__(self, exc_type: object, exc: object, exc_tb: object) -> None:
        return None

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))
        response = self.responses[self.index]
        self.pending = (
            response
            if isinstance(response, Exception)
            else [json.dumps(event) for event in response]
        )
        self.index += 1

    def __aiter__(self) -> "FakeWebSocket":
        return self

    async def __anext__(self) -> str:
        if isinstance(self.pending, Exception):
            exc = self.pending
            self.pending = []
            raise exc
        if not self.pending:
            raise StopAsyncIteration
        return self.pending.pop(0)


def _websocket_config(**overrides: Any) -> Config:
    values = {
        "openai_model": "gpt-5-mini",
        "openai_api_key": "test-key",
        "openai_oauth": "",
        "openai_thinking": "low",
        "openai_fast": False,
        "openai_websocket": True,
    } | overrides
    return cast(Config, SimpleNamespace(**values))


def _patch_api_websocket(
    monkeypatch: pytest.MonkeyPatch,
    websocket: FakeWebSocket,
) -> list[dict[str, Any]]:
    connect_calls: list[dict[str, Any]] = []

    def fake_connect(uri: str, **kwargs: Any) -> FakeWebSocket:
        connect_calls.append({"uri": uri, **kwargs})
        return websocket

    from faltoobot import websockets as websocket_utils

    websocket_utils.WEBSOCKET_SESSIONS.clear()
    monkeypatch.setattr(websocket_utils, "websocket_connect", fake_connect)
    monkeypatch.setattr(websocket_utils, "uses_chatgpt_oauth", lambda config: False)
    return connect_calls


def _previous_response_not_found_event() -> dict[str, Any]:
    return {
        "type": "error",
        "sequence_number": 1,
        "error": {
            "type": "invalid_request_error",
            "code": "previous_response_not_found",
            "message": "Previous response not found",
        },
    }


def _websocket_connection_limit_event() -> list[dict[str, Any]]:
    return [
        {
            "type": "error",
            "sequence_number": 1,
            "error": {
                "type": "invalid_request_error",
                "code": "websocket_connection_limit_reached",
                "message": "Responses websocket connection limit reached.",
            },
        }
    ]


def _invalid_request_error_event(code: str | None = None) -> list[dict[str, Any]]:
    error = {
        "type": "invalid_request_error",
        "message": "Bad websocket request",
    }
    if code is not None:
        # comment: OpenAI invalid_request_error often includes a specific code.
        error["code"] = code
    return [
        {
            "type": "error",
            "sequence_number": 1,
            "error": error,
        }
    ]


def _websocket_completed_response(
    response_id: str, output: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    return [
        {
            "type": "response.completed",
            "sequence_number": 1,
            "response": {
                "id": response_id,
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "gpt-5-mini",
                "output": [] if output is None else output,
            },
        }
    ]


def _websocket_completed_response_without_id() -> list[dict[str, Any]]:
    response = _websocket_completed_response("resp_unused")
    del response[0]["response"]["id"]
    return response


def _invalid_status(status_code: int = 401) -> InvalidStatus:
    return InvalidStatus(Response(status_code, "Unauthorized", Headers()))


def _inline_png_data_url() -> str:
    image = Image.effect_noise((600, 600), 100).convert("RGB")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


@pytest.mark.anyio
async def test_get_streaming_reply_uses_websocket_incremental_tool_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = FakeWebSocket(
        [
            _websocket_completed_response("resp_warm"),
            [
                {
                    "type": "response.output_text.delta",
                    "sequence_number": 1,
                    "item_id": "msg_1",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "Checking.",
                    "logprobs": [],
                },
                {
                    "type": "response.output_item.done",
                    "sequence_number": 2,
                    "output_index": 1,
                    "item": {
                        "type": "function_call",
                        "id": "fc_1",
                        "call_id": "call_1",
                        "name": "greet",
                        "arguments": '{"name":"Faltoobot"}',
                    },
                },
                {
                    "type": "response.completed",
                    "sequence_number": 3,
                    "response": {
                        "id": "resp_1",
                        "object": "response",
                        "created_at": 0,
                        "status": "completed",
                        "model": "gpt-5-mini",
                        "output": [],
                    },
                },
            ],
            [
                {
                    "type": "response.output_text.delta",
                    "sequence_number": 1,
                    "item_id": "msg_2",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "Done.",
                    "logprobs": [],
                },
                {
                    "type": "response.completed",
                    "sequence_number": 2,
                    "response": {
                        "id": "resp_2",
                        "object": "response",
                        "created_at": 0,
                        "status": "completed",
                        "model": "gpt-5-mini",
                        "output": [
                            {
                                "type": "message",
                                "id": "msg_2",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "Done."}],
                            }
                        ],
                    },
                },
            ],
        ]
    )
    connect_calls = _patch_api_websocket(monkeypatch, websocket)
    config = _websocket_config()

    history: MessageHistory = [
        {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}
    ]
    items = [
        item
        async for item in sessions._get_streaming_reply(
            config=config,
            instructions="system prompt",
            input=history,
            tools=[greet],
            prompt_cache_key="session-123",
        )
    ]

    assert [getattr(item, "type", "response") for item in items] == [
        "response.output_text.delta",
        "response.output_item.done",
        "response.completed",
        "function_call_output",
        "response.output_text.delta",
        "response.completed",
    ]
    ssl_context = connect_calls[0].pop("ssl")
    assert isinstance(ssl_context, ssl.SSLContext)
    assert connect_calls == [
        {
            "uri": "wss://api.openai.com/v1/responses",
            "additional_headers": {
                "Authorization": "Bearer test-key",
                "OpenAI-Beta": "responses_websockets=2026-02-06",
            },
            "max_size": 16 * 1024 * 1024,
            "open_timeout": 60.0,
        }
    ]
    assert websocket.sent[0]["type"] == "response.create"
    assert websocket.sent[0]["generate"] is False
    assert websocket.sent[0]["input"] == [
        {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}
    ]
    assert websocket.sent[0]["prompt_cache_key"] == "session-123"
    assert websocket.sent[0]["tool_choice"] == "auto"
    assert websocket.sent[0]["reasoning"] == {"summary": "concise", "effort": "low"}
    assert websocket.sent[0]["tools"][-1] == {"type": "image_generation"}
    assert "stream" not in websocket.sent[0]
    assert "background" not in websocket.sent[0]
    assert websocket.sent[1]["previous_response_id"] == "resp_warm"
    assert websocket.sent[1]["input"] == []
    assert websocket.sent[2]["previous_response_id"] == "resp_1"
    assert websocket.sent[2]["input"] == [
        {
            "id": "fco_call_1",
            "type": "function_call_output",
            "call_id": "call_1",
            "output": "hello Faltoobot",
            "status": "completed",
        }
    ]
    assert history[-2:] == [
        websocket.sent[2]["input"][0],
        {
            "type": "message",
            "id": "msg_2",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Done."}],
            "response_id": "resp_2",
        },
    ]


@pytest.mark.anyio
async def test_websocket_prewarm_reuses_previous_response_across_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = FakeWebSocket(
        [
            [
                {
                    "type": "response.completed",
                    "sequence_number": 1,
                    "response": {
                        "id": "resp_warm",
                        "object": "response",
                        "created_at": 0,
                        "status": "completed",
                        "model": "gpt-5-mini",
                        "output": [],
                    },
                }
            ],
            [
                {
                    "type": "response.completed",
                    "sequence_number": 1,
                    "response": {
                        "id": "resp_1",
                        "object": "response",
                        "created_at": 0,
                        "status": "completed",
                        "model": "gpt-5-mini",
                        "output": [
                            {
                                "type": "message",
                                "id": "msg_1",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "Hello"}],
                            }
                        ],
                    },
                }
            ],
            [
                {
                    "type": "response.completed",
                    "sequence_number": 1,
                    "response": {
                        "id": "resp_2",
                        "object": "response",
                        "created_at": 0,
                        "status": "completed",
                        "model": "gpt-5-mini",
                        "output": [
                            {
                                "type": "message",
                                "id": "msg_2",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "Again"}],
                            }
                        ],
                    },
                }
            ],
        ]
    )
    _patch_api_websocket(monkeypatch, websocket)
    config = _websocket_config()
    prompt_cache_key = "session-prewarm"

    from faltoobot import websockets as websocket_utils

    history: MessageHistory = [{"role": "user", "content": "hello"}]
    await websocket_utils.prewarm(
        config,
        instructions="system prompt",
        input=history,
        tools=[],
        prompt_cache_key=prompt_cache_key,
    )

    _items = [
        item
        async for item in sessions._get_streaming_reply(
            config=config,
            instructions="system prompt",
            input=history,
            tools=[],
            prompt_cache_key=prompt_cache_key,
        )
    ]
    history.append({"role": "user", "content": "again"})
    _items = [
        item
        async for item in sessions._get_streaming_reply(
            config=config,
            instructions="system prompt",
            input=history,
            tools=[],
            prompt_cache_key=prompt_cache_key,
        )
    ]

    assert websocket.sent[0]["generate"] is False
    assert websocket.sent[0]["input"] == [{"role": "user", "content": "hello"}]
    assert websocket.sent[1]["previous_response_id"] == "resp_warm"
    assert websocket.sent[1]["input"] == []
    assert websocket.sent[2]["previous_response_id"] == "resp_1"
    assert websocket.sent[2]["input"] == [{"role": "user", "content": "again"}]


@pytest.mark.anyio
async def test_websocket_prewarm_drops_image_generation_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = FakeWebSocket([_websocket_completed_response("resp_warm")])
    _patch_api_websocket(monkeypatch, websocket)
    config = _websocket_config()

    from faltoobot import websockets as websocket_utils

    history: MessageHistory = [
        {"role": "user", "content": "draw"},
        {
            "type": "image_generation_call",
            "id": "ig_1",
            "status": "completed",
            "action": "generate",
            "background": "opaque",
            "output_format": "png",
            "quality": "medium",
            "result": "base64",
            "revised_prompt": "draw a test image",
            "size": "1122x1402",
        },
    ]
    await websocket_utils.prewarm(
        config,
        instructions="system prompt",
        input=history,
        tools=[],
        prompt_cache_key="session-123",
    )

    assert websocket.sent[0]["input"] == [
        {"role": "user", "content": "draw"},
    ]


@pytest.mark.anyio
async def test_websocket_prewarm_raises_when_response_id_stays_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from faltoobot import websockets as websocket_utils

    websocket = FakeWebSocket(
        [
            _websocket_completed_response_without_id()
            for _ in range(websocket_utils.WEBSOCKET_PREWARM_RETRIES + 1)
        ]
    )
    _patch_api_websocket(monkeypatch, websocket)
    config = _websocket_config()
    history: MessageHistory = [{"role": "user", "content": "hello"}]

    with pytest.raises(websocket_utils.MissingPrewarmResponseIDError):
        _items = [
            item
            async for item in sessions._get_streaming_reply(
                config=config,
                instructions="system prompt",
                input=history,
                tools=[],
                prompt_cache_key="session-123",
            )
        ]

    assert len(websocket.sent) == websocket_utils.WEBSOCKET_PREWARM_RETRIES + 1
    assert all(item["generate"] is False for item in websocket.sent)


@pytest.mark.anyio
async def test_websocket_prewarm_retries_when_response_id_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = FakeWebSocket(
        [
            _websocket_completed_response_without_id(),
            _websocket_completed_response("resp_warm"),
            _websocket_completed_response("resp_1"),
        ]
    )
    _patch_api_websocket(monkeypatch, websocket)
    config = _websocket_config()
    history: MessageHistory = [{"role": "user", "content": "hello"}]

    _items = [
        item
        async for item in sessions._get_streaming_reply(
            config=config,
            instructions="system prompt",
            input=history,
            tools=[],
            prompt_cache_key="session-123",
        )
    ]

    assert websocket.sent[0]["generate"] is False
    assert websocket.sent[1]["generate"] is False
    assert websocket.sent[2]["previous_response_id"] == "resp_warm"
    assert websocket.sent[2]["input"] == []


@pytest.mark.anyio
async def test_websocket_prewarm_resizes_inline_history_after_message_too_big(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exc = ConnectionClosedError(
        Close(1009, "message too big"),
        Close(1009, "message too big"),
        True,
    )
    websocket = FakeWebSocket(
        [
            exc,
            _websocket_completed_response("resp_warm"),
            _websocket_completed_response("resp_1"),
        ]
    )
    _patch_api_websocket(monkeypatch, websocket)
    config = _websocket_config()

    original_url = _inline_png_data_url()
    history: MessageHistory = [
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": [
                {"type": "input_image", "image_url": original_url, "detail": "auto"}
            ],
        }
    ]

    _items = [
        item
        async for item in sessions._get_streaming_reply(
            config=config,
            instructions="system prompt",
            input=history,
            tools=[],
            prompt_cache_key="session-123",
        )
    ]

    healed_url = websocket.sent[1]["input"][0]["output"][0]["image_url"]
    assert websocket.sent[0]["input"][0]["output"][0]["image_url"] == original_url
    assert healed_url.startswith("data:image/jpeg;base64,")
    assert len(healed_url) < len(original_url)
    assert history[0]["output"][0]["image_url"] == healed_url
    assert websocket.sent[2]["previous_response_id"] == "resp_warm"
    assert websocket.sent[2]["input"] == []


@pytest.mark.anyio
async def test_websocket_prewarm_ignores_text_tool_outputs_when_resizing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exc = ConnectionClosedError(
        Close(1009, "message too big"),
        Close(1009, "message too big"),
        True,
    )
    websocket = FakeWebSocket(
        [
            exc,
            _websocket_completed_response("resp_warm"),
            _websocket_completed_response("resp_1"),
        ]
    )
    _patch_api_websocket(monkeypatch, websocket)
    config = _websocket_config()

    original_url = _inline_png_data_url()
    history: MessageHistory = [
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": "loaded image",
        },
        {
            "type": "function_call_output",
            "call_id": "call_2",
            "output": [
                {"type": "input_image", "image_url": original_url, "detail": "auto"},
            ],
        },
    ]

    _items = [
        item
        async for item in sessions._get_streaming_reply(
            config=config,
            instructions="system prompt",
            input=history,
            tools=[],
            prompt_cache_key="session-123",
        )
    ]

    healed_url = websocket.sent[1]["input"][1]["output"][0]["image_url"]
    assert websocket.sent[1]["input"][0]["output"] == "loaded image"
    assert healed_url.startswith("data:image/jpeg;base64,")
    assert history[1]["output"][0]["image_url"] == healed_url


@pytest.mark.anyio
async def test_websocket_prewarm_does_not_retry_client_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = FakeWebSocket([], connect_error=_invalid_status())
    connect_calls = _patch_api_websocket(monkeypatch, websocket)
    config = _websocket_config()

    with pytest.raises(InvalidStatus, match="HTTP 401"):
        _items = [
            item
            async for item in sessions._get_streaming_reply(
                config=config,
                instructions="system prompt",
                input=[{"role": "user", "content": "hello"}],
                tools=[],
                prompt_cache_key="session-123",
            )
        ]

    assert len(connect_calls) == 1
    assert websocket.sent == []


@pytest.mark.anyio
async def test_get_streaming_reply_replays_history_after_websocket_connection_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = FakeWebSocket(
        [
            _websocket_completed_response("resp_warm"),
            _websocket_connection_limit_event(),
            _websocket_completed_response("resp_rewarm"),
            _websocket_completed_response(
                "resp_new",
                [
                    {
                        "type": "message",
                        "id": "msg_new",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Hello"}],
                    }
                ],
            ),
        ]
    )
    _patch_api_websocket(monkeypatch, websocket)
    config = _websocket_config()
    history: MessageHistory = [{"role": "user", "content": "hello"}]

    _items = [
        item
        async for item in sessions._get_streaming_reply(
            config=config,
            instructions="system prompt",
            input=history,
            tools=[],
            prompt_cache_key="session-123",
        )
    ]

    assert websocket.sent[0]["generate"] is False
    assert websocket.sent[1]["previous_response_id"] == "resp_warm"
    assert websocket.sent[1]["input"] == []
    assert "previous_response_id" not in websocket.sent[2]
    assert websocket.sent[2]["generate"] is False
    assert websocket.sent[2]["input"] == [{"role": "user", "content": "hello"}]
    assert websocket.sent[3]["previous_response_id"] == "resp_rewarm"
    assert websocket.sent[3]["input"] == []
    assert history[-1]["response_id"] == "resp_new"


@pytest.mark.anyio
async def test_get_streaming_reply_raises_websocket_client_errors_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = FakeWebSocket([_websocket_completed_response("resp_warm")])
    connect_calls = _patch_api_websocket(monkeypatch, websocket)
    config = _websocket_config()

    from faltoobot import websockets as websocket_utils

    await websocket_utils.prewarm(
        config,
        instructions="system prompt",
        input=[{"role": "user", "content": "hello"}],
        tools=[],
        prompt_cache_key="session-123",
    )
    websocket.connect_error = _invalid_status()
    await websocket_utils._close_session(  # type: ignore[attr-defined]
        websocket_utils.WEBSOCKET_SESSIONS["session-123"]
    )

    with pytest.raises(InvalidStatus, match="HTTP 401"):
        _items = [
            item
            async for item in sessions._get_streaming_reply(
                config=config,
                instructions="system prompt",
                input=[{"role": "user", "content": "hello"}],
                tools=[],
                prompt_cache_key="session-123",
            )
        ]

    assert len(connect_calls) == len(websocket.responses) + 1
    assert len(websocket.sent) == len(websocket.responses)


@pytest.mark.anyio
async def test_get_streaming_reply_does_not_retry_invalid_request_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = FakeWebSocket(
        [
            _websocket_completed_response("resp_warm"),
            _invalid_request_error_event("invalid_value"),
        ]
    )
    _patch_api_websocket(monkeypatch, websocket)
    config = _websocket_config()

    from faltoobot import websockets as websocket_utils

    with pytest.raises(
        websocket_utils.InvalidRequestError, match="Bad websocket request"
    ):
        _items = [
            item
            async for item in sessions._get_streaming_reply(
                config=config,
                instructions="system prompt",
                input=[{"role": "user", "content": "hello"}],
                tools=[],
                prompt_cache_key="session-123",
            )
        ]

    assert len(websocket.sent) == len(websocket.responses)


@pytest.mark.anyio
async def test_get_streaming_reply_replays_history_instead_of_stale_response_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = FakeWebSocket(
        [
            _websocket_completed_response("resp_warm"),
            _websocket_completed_response(
                "resp_new",
                [
                    {
                        "type": "message",
                        "id": "msg_new",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Hello"}],
                    }
                ],
            ),
        ]
    )
    _patch_api_websocket(monkeypatch, websocket)
    config = _websocket_config()

    history: MessageHistory = [
        {"role": "user", "content": "old question"},
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "old answer"}],
            "response_id": "resp_old",
        },
        {"role": "user", "content": "new question"},
    ]
    _items = [
        item
        async for item in sessions._get_streaming_reply(
            config=config,
            instructions="system prompt",
            input=history,
            tools=[],
            prompt_cache_key="session-123",
        )
    ]

    assert "previous_response_id" not in websocket.sent[0]
    assert websocket.sent[0]["generate"] is False
    assert websocket.sent[0]["input"] == [
        {"role": "user", "content": "old question"},
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "old answer"}],
        },
        {"role": "user", "content": "new question"},
    ]
    assert websocket.sent[1]["previous_response_id"] == "resp_warm"
    assert websocket.sent[1]["input"] == []
    assert history[-1]["response_id"] == "resp_new"


@pytest.mark.anyio
async def test_get_streaming_reply_recovers_missing_previous_response_id_after_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = FakeWebSocket(
        [
            _websocket_completed_response("resp_warm"),
            [
                {
                    "type": "response.output_item.done",
                    "sequence_number": 1,
                    "output_index": 0,
                    "item": {
                        "type": "function_call",
                        "id": "fc_1",
                        "call_id": "call_1",
                        "name": "greet",
                        "arguments": '{"name":"Faltoobot"}',
                    },
                },
                {
                    "type": "response.completed",
                    "sequence_number": 2,
                    "response": {
                        "id": "resp_1",
                        "object": "response",
                        "created_at": 0,
                        "status": "completed",
                        "model": "gpt-5-mini",
                        "output": [],
                    },
                },
            ],
            [_previous_response_not_found_event()],
            _websocket_completed_response("resp_rewarm"),
            [
                {
                    "type": "response.completed",
                    "sequence_number": 1,
                    "response": {
                        "id": "resp_2",
                        "object": "response",
                        "created_at": 0,
                        "status": "completed",
                        "model": "gpt-5-mini",
                        "output": [
                            {
                                "type": "message",
                                "id": "msg_2",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "Hello"}],
                            }
                        ],
                    },
                }
            ],
        ]
    )
    _patch_api_websocket(monkeypatch, websocket)
    config = _websocket_config()
    history: MessageHistory = [{"role": "user", "content": "new question"}]

    items = [
        item
        async for item in sessions._get_streaming_reply(
            config=config,
            instructions="system prompt",
            input=history,
            tools=[greet],
            prompt_cache_key="session-123",
        )
    ]

    assert [getattr(item, "type", "response") for item in items] == [
        "response.output_item.done",
        "response.completed",
        "function_call_output",
        "response.completed",
    ]
    assert "previous_response_id" not in websocket.sent[0]
    assert websocket.sent[0]["generate"] is False
    assert websocket.sent[1]["previous_response_id"] == "resp_warm"
    assert websocket.sent[1]["input"] == []
    assert websocket.sent[2]["previous_response_id"] == "resp_1"
    assert websocket.sent[2]["input"] == [
        {
            "id": "fco_call_1",
            "type": "function_call_output",
            "call_id": "call_1",
            "output": "hello Faltoobot",
            "status": "completed",
        }
    ]
    assert "previous_response_id" not in websocket.sent[3]
    assert websocket.sent[3]["generate"] is False
    assert websocket.sent[3]["input"] == [
        {"role": "user", "content": "new question"},
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "greet",
            "arguments": '{"name":"Faltoobot"}',
        },
        websocket.sent[2]["input"][0],
    ]
    assert websocket.sent[4]["previous_response_id"] == "resp_rewarm"
    assert websocket.sent[4]["input"] == []
    assert history[-1] == {
        "type": "message",
        "id": "msg_2",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "Hello"}],
        "response_id": "resp_2",
    }


@pytest.mark.anyio
async def test_get_streaming_reply_raises_when_previous_response_retry_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = FakeWebSocket(
        [
            _websocket_completed_response("resp_warm"),
            [
                {
                    "type": "response.output_item.done",
                    "sequence_number": 1,
                    "output_index": 0,
                    "item": {
                        "type": "function_call",
                        "id": "fc_1",
                        "call_id": "call_1",
                        "name": "greet",
                        "arguments": '{"name":"Faltoobot"}',
                    },
                },
                {
                    "type": "response.completed",
                    "sequence_number": 2,
                    "response": {
                        "id": "resp_1",
                        "object": "response",
                        "created_at": 0,
                        "status": "completed",
                        "model": "gpt-5-mini",
                        "output": [],
                    },
                },
            ],
            [_previous_response_not_found_event()],
            _websocket_completed_response("resp_rewarm"),
            [_previous_response_not_found_event()],
        ]
    )
    _patch_api_websocket(monkeypatch, websocket)
    from faltoobot import websockets as websocket_utils

    monkeypatch.setattr(websocket_utils, "WEBSOCKET_STREAM_RETRIES", 1)
    config = _websocket_config()
    history: MessageHistory = [{"role": "user", "content": "new question"}]

    with pytest.raises(RuntimeError, match="previous_response_not_found"):
        _items = [
            item
            async for item in sessions._get_streaming_reply(
                config=config,
                instructions="system prompt",
                input=history,
                tools=[greet],
                prompt_cache_key="session-123",
            )
        ]

    assert len(websocket.sent) == len(websocket.responses)
    assert websocket.sent[1]["previous_response_id"] == "resp_warm"
    assert websocket.sent[2]["previous_response_id"] == "resp_1"
    assert "previous_response_id" not in websocket.sent[3]
    assert websocket.sent[3]["generate"] is False
    assert websocket.sent[4]["previous_response_id"] == "resp_rewarm"


@pytest.mark.anyio
async def test_get_streaming_reply_uses_oauth_websocket_url_and_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = FakeWebSocket(
        [
            _websocket_completed_response("resp_oauth_warm"),
            _websocket_completed_response("resp_oauth"),
        ]
    )
    connect_calls: list[dict[str, Any]] = []

    def fake_connect(uri: str, **kwargs: Any) -> FakeWebSocket:
        connect_calls.append({"uri": uri, **kwargs})
        return websocket

    async def oauth_token() -> str:
        return "oauth-token"

    from faltoobot import websockets as websocket_utils

    websocket_utils.WEBSOCKET_SESSIONS.clear()
    monkeypatch.setattr(websocket_utils, "websocket_connect", fake_connect)
    monkeypatch.setattr(
        websocket_utils,
        "get_openai_client_options",
        lambda config: (
            oauth_token,
            "https://chatgpt.com/backend-api/codex/",
            {"chatgpt-account-id": "acct_123", "originator": "codex_cli_rs"},
        ),
    )
    config = _websocket_config(openai_api_key="", openai_oauth="auth.json")

    _items = [
        item
        async for item in sessions._get_streaming_reply(
            config=config,
            instructions="system prompt",
            input=[{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
            tools=[],
            prompt_cache_key="session-123",
        )
    ]

    ssl_context = connect_calls[0].pop("ssl")
    assert isinstance(ssl_context, ssl.SSLContext)
    assert connect_calls == [
        {
            "uri": "wss://chatgpt.com/backend-api/codex/responses",
            "additional_headers": {
                "Authorization": "Bearer oauth-token",
                "OpenAI-Beta": "responses_websockets=2026-02-06",
                "chatgpt-account-id": "acct_123",
                "originator": "codex_cli_rs",
                "session-id": "session-123",
                "thread-id": "session-123",
                "x-client-request-id": "session-123",
            },
            "max_size": 16 * 1024 * 1024,
            "open_timeout": 60.0,
        }
    ]
