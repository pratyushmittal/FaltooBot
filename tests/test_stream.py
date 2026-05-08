from types import SimpleNamespace
from typing import Any, cast

import pytest

from faltoobot.faltoochat.stream import get_event_text


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (
            SimpleNamespace(
                type="response.output_item.done",
                item={
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "hello"}],
                },
            ),
            (True, "tool", ""),
        ),
        (
            SimpleNamespace(
                type="response.output_item.done",
                item={
                    "type": "function_call",
                    "name": "run_shell_call",
                    "arguments": '{"command":"rg -n foobar faltoobot tests","timeout_ms":10000}',
                },
            ),
            (True, "tool", "searching for foobar in faltoobot tests"),
        ),
        (
            SimpleNamespace(
                type="response.reasoning_summary_part.added",
                part=SimpleNamespace(text="**Planning** reply"),
            ),
            (True, "thinking", "**Planning** reply"),
        ),
        (
            SimpleNamespace(
                type="codex.rate_limits",
                plan_type="prolite",
                rate_limits={
                    "primary": {"used_percent": 13, "reset_after_seconds": 1294},
                    "secondary": {"used_percent": 37, "reset_after_seconds": 283462},
                },
            ),
            (
                True,
                "tool",
                "Rate limits: primary 13% used",
            ),
        ),
    ],
)
def test_get_event_text(event: object, expected: tuple[bool, str, str]) -> None:
    assert get_event_text(cast(Any, event)) == expected
