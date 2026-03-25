import re

from .diff import Diff

WORD_PATTERN = re.compile(r"[A-Za-z0-9_]+")


def next_word_location(
    text: str, cursor_location: tuple[int, int]
) -> tuple[int, int] | None:
    """Return the next word start after the cursor."""
    lines = text.splitlines()
    line_index, column = cursor_location
    for index in range(line_index, len(lines)):
        line = lines[index]
        start_column = column + 1 if index == line_index else 0
        for match in WORD_PATTERN.finditer(line):
            if match.start() >= start_column:
                return index, match.start()
    return None


def previous_word_location(
    text: str, cursor_location: tuple[int, int]
) -> tuple[int, int] | None:
    """Return the previous word start before the cursor."""
    lines = text.splitlines()
    line_index, column = cursor_location
    for index in range(line_index, -1, -1):
        line = lines[index]
        end_column = column - 1 if index == line_index else len(line) - 1
        for match in reversed(list(WORD_PATTERN.finditer(line))):
            if match.start() <= end_column:
                return index, match.start()
    return None


def word_under_cursor(text: str, column: int) -> str | None:
    """Return the word under the given column on a line of text."""
    if not text:
        return None
    text_column = min(column, len(text) - 1)
    for match in WORD_PATTERN.finditer(text):
        if match.start() <= text_column < match.end():
            return match.group(0)
    if column == 0:
        match = WORD_PATTERN.search(text)
        if match is not None:
            return match.group(0)
    return None


def next_modification(diff: Diff, cursor_line: int) -> int | None:
    """Return the next unstaged modified line, wrapping to the top."""
    lines = [
        index
        for index, line in enumerate(diff)
        if line["type"] in {"+", "-"} and not line["is_staged"]
    ]
    for line in lines:
        if line > cursor_line:
            return line
    return None if not lines else lines[0]


def previous_modification(diff: Diff, cursor_line: int) -> int | None:
    """Return the previous unstaged modified line, wrapping to the bottom."""
    lines = [
        index
        for index, line in enumerate(diff)
        if line["type"] in {"+", "-"} and not line["is_staged"]
    ]
    for line in reversed(lines):
        if line < cursor_line:
            return line
    return None if not lines else lines[-1]


def _search_match_columns(text: str, term: str, *, whole_word: bool) -> list[int]:
    if not term:
        return []
    if not whole_word:
        lower_text = text.lower()
        lower_term = term.lower()
        start = 0
        columns: list[int] = []
        while True:
            index = lower_text.find(lower_term, start)
            if index == -1:
                return columns
            columns.append(index)
            start = index + 1
    return [
        match.start()
        for match in re.finditer(rf"\b{re.escape(term)}\b", text, flags=re.IGNORECASE)
    ]


def _matches_search(text: str, term: str, *, whole_word: bool) -> bool:
    """Check if a line matches the current search term."""
    return bool(_search_match_columns(text, term, whole_word=whole_word))


def next_search_location(
    diff: Diff,
    term: str,
    cursor_location: tuple[int, int],
    *,
    whole_word: bool = False,
) -> tuple[int, int] | None:
    """Return the next search match location, wrapping to the top."""
    line_index, column = cursor_location
    for index in range(line_index, len(diff)):
        start_column = column + 1 if index == line_index else 0
        for match_column in _search_match_columns(
            diff[index]["text"],
            term,
            whole_word=whole_word,
        ):
            if match_column >= start_column:
                return index, match_column
    for index, line in enumerate(diff):
        matches = _search_match_columns(line["text"], term, whole_word=whole_word)
        if matches:
            return index, matches[0]
    return None


def previous_search_location(
    diff: Diff,
    term: str,
    cursor_location: tuple[int, int],
    *,
    whole_word: bool = False,
) -> tuple[int, int] | None:
    """Return the previous search match location, wrapping to the bottom."""
    line_index, column = cursor_location
    for index in range(line_index, -1, -1):
        end_column = column - 1 if index == line_index else len(diff[index]["text"])
        for match_column in reversed(
            _search_match_columns(diff[index]["text"], term, whole_word=whole_word)
        ):
            if match_column < end_column:
                return index, match_column
    for index in range(len(diff) - 1, -1, -1):
        matches = _search_match_columns(
            diff[index]["text"], term, whole_word=whole_word
        )
        if matches:
            return index, matches[-1]
    return None


def next_search_line(
    diff: Diff,
    term: str,
    cursor_line: int,
    *,
    whole_word: bool = False,
) -> int | None:
    """Return the next matching line, wrapping to the top."""
    if cursor_line < 0:
        location = next_search_location(diff, term, (0, -1), whole_word=whole_word)
    else:
        location = next_search_location(
            diff,
            term,
            (cursor_line, -1),
            whole_word=whole_word,
        )
    return None if location is None else location[0]


def previous_search_line(
    diff: Diff,
    term: str,
    cursor_line: int,
    *,
    whole_word: bool = False,
) -> int | None:
    """Return the previous matching line, wrapping to the bottom."""
    location = previous_search_location(
        diff,
        term,
        (cursor_line, len(diff[cursor_line]["text"]) if diff else 0),
        whole_word=whole_word,
    )
    return None if location is None else location[0]
