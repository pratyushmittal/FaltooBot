import pytest

from faltoobot.faltoochat.messages_rendering import get_item_text


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        pytest.param(
            {
                "type": "function_call",
                "name": "run_shell_call",
                "arguments": '{"command":"sed -n \'1,220p\' faltoobot/chat.py","timeout_ms":10000}',
            },
            ("reading faltoobot/chat.py 1 to 220", "tool"),
            id="summarizes-sed-command",
        ),
        pytest.param(
            {
                "type": "function_call",
                "name": "run_shell_call",
                "arguments": '{"command":"cd /tmp && rg -n foobar faltoobot tests","timeout_ms":10000}',
            },
            ("searching for foobar in faltoobot tests", "tool"),
            id="summarizes-rg-command",
        ),
        pytest.param(
            {
                "type": "function_call",
                "name": "run_shell_call",
                "arguments": '{"command":"git status --short","command_summary":"checking git status","timeout_ms":10000}',
            },
            (
                "**Shell:** checking git status\n\n<!-- shell-command -->\n\ngit status --short",
                "tool",
            ),
            id="prefers-command-summary",
        ),
    ],
)
def test_get_item_text_renders_run_shell_call_items(
    item: dict[str, object], expected: tuple[str, str]
) -> None:
    assert get_item_text(item) == expected


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        pytest.param(
            {
                "type": "reasoning",
                "summary": [
                    {
                        "type": "summary_text",
                        "text": "**Planning** reply\n\nMore detail",
                    }
                ],
            },
            ("**Planning**", "thinking"),
            id="keeps-only-bold-summary",
        ),
        pytest.param(
            {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "Planning reply"}],
            },
            ("Planning reply", "thinking"),
            id="keeps-plain-summary-without-bold",
        ),
        pytest.param(
            {
                "type": "reasoning",
                "summary": [
                    {
                        "type": "summary_text",
                        "text": "**Estimating sunset time**\n\nThe user asked for a guess.",
                    },
                    {
                        "type": "summary_text",
                        "text": "**Calculating sunset time**\n\nOn March 23 I estimate...",
                    },
                ],
            },
            (
                "**Estimating sunset time**\n**Calculating sunset time**",
                "thinking",
            ),
            id="keeps-bold-summaries-from-multiple-parts",
        ),
    ],
)
def test_get_item_text_renders_reasoning_items(
    item: dict[str, object], expected: tuple[str, str]
) -> None:
    assert get_item_text(item) == expected
