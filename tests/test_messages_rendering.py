from faltoobot.messages_rendering import get_item_text


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
