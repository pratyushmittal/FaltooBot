from faltoobot.macchat.app import edit_menu_items, should_submit_for_selector
from faltoobot.chat.entries import Entry
from faltoobot.macchat.formatting import (
    entry_text,
    queue_text,
    status_line,
    transcript_text,
)


def test_entry_text_prefixes_multiline_content() -> None:
    text = entry_text(Entry("bot", "Hello\nWorld"))

    assert text == "Faltoobot: Hello\n    World"


def test_transcript_text_skips_empty_entries() -> None:
    text = transcript_text([Entry("meta", ""), Entry("you", "Ping")])

    assert text == "You: Ping"


def test_queue_text_uses_preview_and_markers() -> None:
    text = queue_text([("first line\nsecond line", False), ("later", True)])

    assert text == "☑︎ first line second line\n□ later"


def test_status_line_adds_runtime_flags() -> None:
    assert status_line("model: gpt-5.4", replying=True, queued=2) == (
        "model: gpt-5.4  replying  queued 2"
    )


def test_edit_menu_includes_paste_shortcut() -> None:
    assert ("Paste", "paste:", "v") in edit_menu_items()


def test_return_selector_submits_prompt() -> None:
    assert should_submit_for_selector("insertNewline:") is True
    assert should_submit_for_selector("insertTab:") is False
