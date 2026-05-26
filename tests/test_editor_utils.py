from faltoobot.faltoochat.diff import Diff
from faltoobot.faltoochat.editor_utils import (
    next_modification,
    next_search_line,
    previous_modification,
    word_under_cursor,
)


def test_next_modification_jumps_to_next_block_start() -> None:
    diff: Diff = [
        {"is_staged": False, "type": "", "text": "ctx"},
        {"is_staged": True, "type": "-", "text": "staged old"},
        {"is_staged": False, "type": "+", "text": "unstaged new"},
        {"is_staged": False, "type": "+", "text": "unstaged new 2"},
        {"is_staged": False, "type": "", "text": "ctx2"},
        {"is_staged": False, "type": "-", "text": "next old"},
    ]

    first_block_start = 2
    second_block_start = 5

    assert next_modification(diff, 0) == first_block_start
    assert next_modification(diff, first_block_start) == second_block_start
    assert next_modification(diff, first_block_start + 1) == second_block_start


def test_next_search_line_can_match_whole_words_only() -> None:
    diff: Diff = [
        {"is_staged": False, "type": "", "text": "alphabetabeta"},
        {"is_staged": False, "type": "", "text": "beta"},
    ]

    assert next_search_line(diff, "beta", -1, whole_word=True) == 1
    assert next_search_line(diff, "beta", -1, whole_word=False) == 0


def test_previous_modification_jumps_to_previous_block_start() -> None:
    diff: Diff = [
        {"is_staged": False, "type": "-", "text": "first old"},
        {"is_staged": False, "type": "+", "text": "first new"},
        {"is_staged": False, "type": "", "text": "ctx"},
        {"is_staged": True, "type": "+", "text": "staged new"},
        {"is_staged": False, "type": "-", "text": "unstaged old"},
        {"is_staged": False, "type": "+", "text": "unstaged new"},
    ]

    assert previous_modification(diff, 5) == 0
    assert previous_modification(diff, 4) == 0


def test_word_under_cursor_uses_current_word() -> None:
    assert word_under_cursor('value = "beta staged"', 10) == "beta"
