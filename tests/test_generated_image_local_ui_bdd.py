import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from PIL import Image
from pytest_bdd import given, scenarios, then, when

from openai.types.responses import ResponseOutputMessage, ResponseOutputText
from openai.types.responses.response_output_item import ImageGenerationCall

from faltoobot import sessions
from faltoobot.gpt_utils import MessageHistory

scenarios("features/generated_image_local_ui.feature")


@pytest.fixture
def image_ui_ctx(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    monkeypatch.setattr(sessions, "build_config", lambda: SimpleNamespace())
    monkeypatch.setattr(
        sessions,
        "get_system_instructions",
        lambda config, chat_key, workspace: "system prompt",
    )
    return {"tmp_path": tmp_path, "events": []}


def _image_call(tmp_path: Path) -> ImageGenerationCall:
    image = tmp_path / "source.png"
    Image.new("RGB", (4, 4), color="red").save(image)
    return ImageGenerationCall(
        id="ig_test",
        result=base64.b64encode(image.read_bytes()).decode("utf-8"),
        status="completed",
        type="image_generation_call",
    )


def _output_message() -> ResponseOutputMessage:
    return ResponseOutputMessage(
        id="msg_test",
        type="message",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text="done", annotations=[])],
    )


@given("a Faltoochat session with a mocked generated image response")
def faltoochat_session(
    image_ui_ctx: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_path = cast(Path, image_ui_ctx["tmp_path"])
    image_call = _image_call(tmp_path)

    async def fake_get_streaming_reply(
        config: Any,
        instructions: str,
        input: MessageHistory,
        tools: list[Any],
        prompt_cache_key: str | None = None,
    ):
        input.append(
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "done"}],
            }
        )
        yield SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                output=[_output_message(), image_call], output_text=""
            ),
        )

    monkeypatch.setattr(sessions, "_get_streaming_reply", fake_get_streaming_reply)
    image_ui_ctx["session"] = sessions.get_session(
        chat_key="code@test", workspace=tmp_path
    )


@given("a Faltoochat session with a mocked image-only response")
def faltoochat_session_with_image_only_response(
    image_ui_ctx: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_path = cast(Path, image_ui_ctx["tmp_path"])
    image_call = _image_call(tmp_path)

    async def fake_get_streaming_reply(
        config: Any,
        instructions: str,
        input: MessageHistory,
        tools: list[Any],
        prompt_cache_key: str | None = None,
    ):
        input.append(
            {
                "type": "image_generation_call",
                "id": image_call.id,
                "status": image_call.status,
                "result": image_call.result,
            }
        )
        yield SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(output=[image_call], output_text=""),
        )

    monkeypatch.setattr(sessions, "_get_streaming_reply", fake_get_streaming_reply)
    image_ui_ctx["session"] = sessions.get_session(
        chat_key="code@test", workspace=tmp_path
    )


@when("I ask to generate an image for the local UI")
def ask_to_generate_image(image_ui_ctx: dict[str, Any]) -> None:
    async def run() -> list[Any]:
        session = cast(sessions.Session, image_ui_ctx["session"])
        await sessions.append_user_turn(session, question="generate an image of a cat")
        return [event async for event in sessions.get_answer_streaming(session)]

    image_ui_ctx["events"] = asyncio.run(run())


@then("the generated image is saved in the workspace")
def generated_image_is_saved(image_ui_ctx: dict[str, Any]) -> None:
    tmp_path = cast(Path, image_ui_ctx["tmp_path"])
    saved = list((tmp_path / sessions.GENERATED_IMAGES_DIR).glob("*.png"))
    (saved_file,) = saved
    assert saved_file.read_bytes()


@then("the streamed answer includes a generated image markdown link")
def streamed_answer_includes_markdown(image_ui_ctx: dict[str, Any]) -> None:
    assert any(
        "![Generated image](.generated-images/" in getattr(event, "delta", "")
        for event in cast(list[Any], image_ui_ctx["events"])
        if event.type == "response.output_text.delta"
    )


@then("the completed response includes a generated image markdown link")
def completed_response_includes_markdown(image_ui_ctx: dict[str, Any]) -> None:
    completed = next(
        event
        for event in cast(list[Any], image_ui_ctx["events"])
        if event.type == "response.completed"
    )
    text = sessions._output_text(completed.response, completed.response.output)
    assert "done\n\n![Generated image](.generated-images/" in text


@then("the chat history includes a display-only generated image markdown link")
def chat_history_includes_display_only_markdown(image_ui_ctx: dict[str, Any]) -> None:
    session = cast(sessions.Session, image_ui_ctx["session"])
    messages = sessions.get_messages(session)["messages"]
    latest = messages[-1]
    content = latest.get("content")
    assert isinstance(content, list)
    text_part, image_part = content
    assert text_part["text"] == "done"
    assert "![Generated image](.generated-images/" in image_part["text"]
    assert image_part[sessions.DISPLAY_ONLY_CONTENT_KEY] is True


@then("the latest chat history item is a display-only generated image markdown link")
def latest_chat_history_item_is_display_only_markdown(
    image_ui_ctx: dict[str, Any],
) -> None:
    session = cast(sessions.Session, image_ui_ctx["session"])
    latest = sessions.get_messages(session)["messages"][-1]
    assert latest["role"] == "assistant"
    content = latest.get("content")
    assert isinstance(content, list)
    (image_part,) = content
    assert "![Generated image](.generated-images/" in image_part["text"]
    assert image_part[sessions.DISPLAY_ONLY_CONTENT_KEY] is True
