from typing import Any

import pytest
from pytest_bdd import given, scenarios, then, when

from faltoobot import gpt_utils

scenarios("features/store_false_image_generation_replay.feature")


@pytest.fixture
def image_replay_ctx() -> dict[str, Any]:
    return {}


@given("a completed generated image call with response-only metadata")
def generated_image_call_with_metadata(image_replay_ctx: dict[str, Any]) -> None:
    image_replay_ctx["history"] = [
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
        {"role": "user", "content": "thanks"},
    ]


@when("the history is trimmed for a follow-up")
def trim_history_for_follow_up(image_replay_ctx: dict[str, Any]) -> None:
    image_replay_ctx["trimmed"] = gpt_utils.trim_input(image_replay_ctx["history"])


@then("the generated image call is replayed with only OpenAI input fields")
def image_call_is_sanitized(image_replay_ctx: dict[str, Any]) -> None:
    assert image_replay_ctx["trimmed"] == [
        {"role": "user", "content": "draw"},
        {
            "type": "image_generation_call",
            "id": "ig_1",
            "status": "completed",
            "result": "base64",
        },
        {"role": "user", "content": "thanks"},
    ]


@given("a streamed generated image call with a result")
def streamed_image_call_with_result(image_replay_ctx: dict[str, Any]) -> None:
    image_replay_ctx["response_item"] = {
        "type": "image_generation_call",
        "id": "ig_1",
        "status": "generating",
        "result": "base64",
    }


@when("the response item is stored in history")
def store_response_item(image_replay_ctx: dict[str, Any]) -> None:
    image_replay_ctx["stored"] = gpt_utils._to_message_item(
        image_replay_ctx["response_item"]
    )


@then("the stored image call keeps the OpenAI status")
def stored_image_call_keeps_openai_status(image_replay_ctx: dict[str, Any]) -> None:
    stored = image_replay_ctx["stored"]
    assert stored["type"] == "image_generation_call"
    assert stored["id"] == "ig_1"
    assert stored["status"] == "generating"
    assert stored["result"] == "base64"
    assert "timestamp" not in image_replay_ctx["response_item"]
