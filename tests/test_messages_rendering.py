from faltoobot.faltoochat.messages_rendering import get_item_text


def test_get_item_text_summarizes_run_shell_call_sed_commands() -> None:
    rendering = get_item_text(
        {
            "type": "function_call",
            "name": "run_shell_call",
            "arguments": '{"command":"sed -n \'1,220p\' faltoobot/chat.py","timeout_ms":10000}',
        }
    )

    assert rendering == ("reading faltoobot/chat.py 1 to 220", "tool")


def test_get_item_text_summarizes_run_shell_call_rg_commands() -> None:
    rendering = get_item_text(
        {
            "type": "function_call",
            "name": "run_shell_call",
            "arguments": '{"command":"cd /tmp && rg -n foobar faltoobot tests","timeout_ms":10000}',
        }
    )

    assert rendering == (
        "searching for foobar in faltoobot tests",
        "tool",
    )


def test_get_item_text_keeps_only_bold_thinking_summary() -> None:
    rendering = get_item_text(
        {
            "type": "reasoning",
            "summary": [
                {"type": "summary_text", "text": "**Planning** reply\n\nMore detail"}
            ],
        }
    )

    assert rendering == ("**Planning**", "thinking")


def test_get_item_text_keeps_plain_thinking_summary_when_no_bold_exists() -> None:
    rendering = get_item_text(
        {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "Planning reply"}],
        }
    )

    assert rendering == ("Planning reply", "thinking")


def test_get_item_text_keeps_only_bold_thinking_summaries_from_multiple_parts() -> None:
    rendering = get_item_text(
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
        }
    )

    assert rendering == (
        "**Estimating sunset time**\n**Calculating sunset time**",
        "thinking",
    )
