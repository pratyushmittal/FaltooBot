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
                type="faltoobot.post_response_hook.status",
                hook_name="Refactor Code",
                status="running",
            ),
            (True, "tool", "Running post-response hook: Refactor Code"),
        ),
        (
            SimpleNamespace(
                type="faltoobot.post_response_hook.feedback",
                feedback="## Post-response hook feedback\n\n### Refactor\n\nfull feedback",
            ),
            (
                True,
                "tool",
                "## Post-response hook feedback\n\n### Refactor\n\nfull feedback",
            ),
        ),
        (
            SimpleNamespace(
                type="codex.response.metadata",
                headers={
                    "x-codex-safety-buffering-enabled": "true",
                    "x-codex-safety-buffering-faster-model": "gpt-5.6-luna",
                },
            ),
            (False, "", ""),
        ),
        (
            SimpleNamespace(
                type="codex.rate_limits",
                plan_type="prolite",
                rate_limits={
                    "primary": {"used_percent": 2, "reset_after_seconds": 18000},
                    "secondary": {"used_percent": 60, "reset_after_seconds": 604800},
                },
            ),
            (
                True,
                "tool",
                "Remaining limit: 5h = 98% ・ 7d = 40%",
            ),
        ),
        (
            SimpleNamespace(
                type="codex.rate_limits",
                plan_type="prolite",
                rate_limits={
                    "primary": True,
                    "secondary": {"used_percent": False, "reset_after_seconds": 10},
                    "tertiary": {"used_percent": 17},
                },
            ),
            (
                True,
                "tool",
                "Remaining limit: tertiary = 83%",
            ),
        ),
    ],
)
def test_get_event_text(event: object, expected: tuple[bool, str, str]) -> None:
    assert get_event_text(cast(Any, event)) == expected
