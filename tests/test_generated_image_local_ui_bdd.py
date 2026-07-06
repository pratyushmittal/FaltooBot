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

from faltoobot import gpt_utils, sessions
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


def _image_call(
    tmp_path: Path, image_id: str = "ig_test", color: str = "red"
) -> ImageGenerationCall:
    image = tmp_path / f"source_{image_id}.png"
    Image.new("RGB", (4, 4), color=color).save(image)
    return ImageGenerationCall(
        id=image_id,
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
        input.extend(
            [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "done"}],
                },
                {
                    "type": "image_generation_call",
                    "id": image_call.id,
                    "status": image_call.status,
                    "result": image_call.result,
                },
            ]
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


@given("a Faltoochat session with a mocked multiple generated image response")
def faltoochat_session_with_multiple_images(
    image_ui_ctx: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_path = cast(Path, image_ui_ctx["tmp_path"])
    image_calls = [
        _image_call(tmp_path, "ig_one", "red"),
        _image_call(tmp_path, "ig_two", "blue"),
    ]

    async def fake_get_streaming_reply(
        config: Any,
        instructions: str,
        input: MessageHistory,
        tools: list[Any],
        prompt_cache_key: str | None = None,
    ):
        input.extend(
            {
                "type": "image_generation_call",
                "id": image_call.id,
                "status": image_call.status,
                "result": image_call.result,
            }
            for image_call in image_calls
        )
        yield SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                output=[_output_message(), *image_calls], output_text=""
            ),
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


@then("the completed OpenAI response does not include the display-only markdown")
def completed_response_excludes_display_only_markdown(
    image_ui_ctx: dict[str, Any],
) -> None:
    completed = next(
        event
        for event in cast(list[Any], image_ui_ctx["events"])
        if event.type == "response.completed"
    )
    text = sessions._output_text(completed.response, completed.response.output)
    assert text == "done"


@then("the chat history includes a display-only generated image markdown link")
def chat_history_includes_display_only_markdown(image_ui_ctx: dict[str, Any]) -> None:
    session = cast(sessions.Session, image_ui_ctx["session"])
    messages = sessions.get_messages(session)["messages"]
    assistant = next(
        item
        for item in messages
        if item.get("role") == "assistant"
        and item.get(sessions.DISPLAY_ONLY_CONTENT_KEY)
    )
    content = assistant.get("content")
    assert isinstance(content, list)
    (image_part,) = content
    assert "![Generated image](.generated-images/" in image_part["text"]
    assert assistant[sessions.DISPLAY_ONLY_CONTENT_KEY] is True


@then("the chat history includes a developer note with the generated image path")
def chat_history_includes_developer_image_note(image_ui_ctx: dict[str, Any]) -> None:
    session = cast(sessions.Session, image_ui_ctx["session"])
    messages = gpt_utils.trim_input(sessions.get_messages(session)["messages"])
    image_count = sum(
        1 for item in messages if item.get("type") == "image_generation_call"
    )
    developer_paths: list[str] = []
    prefix = "Generated images are saved locally at: "
    for item in messages:
        if item.get("type") != "message" or item.get("role") != "developer":
            continue
        content = item.get("content")
        assert isinstance(content, list)
        assert content[0]["type"] == "input_text"
        text = content[0]["text"]
        assert text.startswith(prefix)
        developer_paths.append(text.removeprefix(prefix))

    tmp_path = cast(Path, image_ui_ctx["tmp_path"])
    saved_paths = {
        path.relative_to(tmp_path).as_posix()
        for path in (tmp_path / sessions.GENERATED_IMAGES_DIR).glob("*.png")
    }
    assert image_count
    assert len(developer_paths) == image_count
    assert set(developer_paths) == saved_paths


@then("the latest chat history item is a display-only generated image markdown link")
def latest_chat_history_item_is_display_only_markdown(
    image_ui_ctx: dict[str, Any],
) -> None:
    session = cast(sessions.Session, image_ui_ctx["session"])
    latest = sessions.get_messages(session)["messages"][-1]
    assert latest["role"] == "assistant"
    assert latest[sessions.DISPLAY_ONLY_CONTENT_KEY] is True
    content = latest.get("content")
    assert isinstance(content, list)
    (image_part,) = content
    assert "![Generated image](.generated-images/" in image_part["text"]
