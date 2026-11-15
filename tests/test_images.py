from faltoobot.images import (
    INLINE_IMAGE_HISTORY_PLACEHOLDER,
    discard_inline_images_from_completed_tool_outputs,
)


def test_discard_inline_images_replaces_completed_tool_payload() -> None:
    history = [
        {
            "type": "function_call_output",
            "call_id": "call_image",
            "output": [
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64,AAAABBBB",
                    "detail": "auto",
                }
            ],
        },
        {
            "type": "function_call_output",
            "call_id": "call_text",
            "output": "ordinary tool output",
        },
    ]

    assert discard_inline_images_from_completed_tool_outputs(history) == 1
    assert history[0]["output"] == INLINE_IMAGE_HISTORY_PLACEHOLDER
    assert history[1]["output"] == "ordinary tool output"


def test_discard_inline_images_keeps_uploaded_file_references() -> None:
    history = [
        {
            "type": "function_call_output",
            "call_id": "call_image",
            "output": [{"type": "input_image", "file_id": "file_123", "detail": "auto"}],
        }
    ]

    assert discard_inline_images_from_completed_tool_outputs(history) == 0
    assert history[0]["output"][0]["file_id"] == "file_123"


def test_discard_inline_images_keeps_mixed_tool_outputs() -> None:
    history = [
        {
            "type": "function_call_output",
            "call_id": "call_mixed",
            "output": [
                {"type": "input_text", "text": "keep me"},
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64,AAAA",
                    "detail": "auto",
                },
            ],
        }
    ]

    assert discard_inline_images_from_completed_tool_outputs(history) == 0
    assert history[0]["output"][0]["text"] == "keep me"
