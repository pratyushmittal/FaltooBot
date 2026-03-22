from types import SimpleNamespace

from faltoobot.stream import get_event_text


def test_get_event_text_ignores_non_tool_output_item_done() -> None:
    event = SimpleNamespace(
        type="response.output_item.done",
        item={
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "hello"}],
        },
    )
    is_new, classes, text = get_event_text(event)  # type: ignore[arg-type]

    assert (is_new, classes, text) == (True, "tool", "")


def test_get_event_text_summarizes_streamed_shell_calls() -> None:
    event = SimpleNamespace(
        type="response.output_item.done",
        item={
            "type": "function_call",
            "name": "run_shell_call",
            "arguments": '{"command":"rg -n foobar faltoobot tests","timeout_ms":10000}',
        },
    )
    is_new, classes, text = get_event_text(event)  # type: ignore[arg-type]

    assert (is_new, classes, text) == (
        True,
        "tool",
        "searching for foobar in faltoobot tests",
    )
