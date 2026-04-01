import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING

from rich.segment import Segment
from rich.style import Style
from tree_sitter import Language
import tree_sitter_lua
import tree_sitter_typescript
from textual import events
from textual.binding import Binding
from textual.strip import Strip
from textual.widgets import TabbedContent, TabPane, TextArea

from ..diff import Diff, get_diff
from ..editor_utils import (
    next_modification,
    next_search_location,
    next_word_location,
    previous_modification,
    previous_search_location,
    previous_word_location,
    word_under_cursor,
)
from ..git import apply_selected_diff_lines, get_selected_change_state, stage_file
from ..review_api import get_review


from .review_comment_modal import ReviewCommentModal
from .search_in_file import SearchInFile

if TYPE_CHECKING:
    from ..review import ReviewView

TAB_SWITCH_COOLDOWN = 0.2


class ReviewDiffView(TextArea):
    BINDINGS = [
        Binding("j,ctrl+n", "review_cursor_down", priority=True, show=False),
        Binding("k,ctrl+p", "review_cursor_up", priority=True, show=False),
        Binding("h", "cursor_left", priority=True, show=False),
        Binding("l", "cursor_right", priority=True, show=False),
        Binding("g", "review_scroll_home", priority=True, show=False),
        Binding("G", "review_scroll_end", priority=True, show=False),
        Binding("w", "review_next_word", priority=True, show=False),
        Binding("b", "review_previous_word", priority=True, show=False),
        Binding("tab", "review_next_file_tab", priority=True, show=True),
        Binding("shift+tab", "review_previous_file_tab", priority=True, show=False),
        Binding(
            "r", "review_refresh_current_file", "Refresh", priority=True, show=True
        ),
        Binding("]", "review_next_modification", "Next Edit", priority=True, show=True),
        Binding(
            "[", "review_previous_modification", "Prev Edit", priority=True, show=True
        ),
        Binding("V", "review_select_line", "Select Line", priority=True, show=True),
        Binding("n", "review_jump_next", "Next Search", priority=True, show=True),
        Binding("N", "review_jump_previous", "Prev Search", priority=True, show=True),
        Binding("*", "review_search_word_under_cursor", priority=True, show=False),
        Binding("slash", "review_search", "Search", priority=True, show=True),
        Binding("escape", "review_escape", "Leave Search", priority=True, show=True),
        Binding("a,c", "review_add", priority=True, show=True),
        Binding("s", "review_stage_lines", priority=True, show=True),
        Binding("S", "review_stage_file", "Stage File", priority=True, show=True),
        Binding("shift+enter", "review_submit_reviews", priority=True, show=True),
    ]

    def __init__(
        self,
        diff: Diff,
        *,
        file_path: Path,
        review_view: "ReviewView",
        **kwargs,
    ) -> None:
        requested_language = kwargs.pop("language", None)
        self.file_path = file_path
        self.review_view = review_view
        self.diff = diff
        self.last_tab_switch_at = 0.0
        self.line_selection_anchor: int | None = None
        self.line_selection_cursor: int | None = None
        self.missing_language_package: str | None = None
        super().__init__(_diff_text(diff), language=None, **kwargs)
        _register_extra_languages(self)
        if requested_language in self.available_languages:
            self.language = requested_language
        elif requested_language is not None:
            self.missing_language_package = _language_package(requested_language)
        self.border_title = "0 comments"

    @property
    def gutter_width(self) -> int:
        """Return the TextArea gutter width plus one column for the diff marker."""
        if not self.show_line_numbers:
            return 0
        return super().gutter_width + 1

    def on_mount(self) -> None:
        if self.missing_language_package is None:
            return
        self.app.notify(
            f"Install `{self.missing_language_package}` for {self.file_path.suffix} syntax highlighting.",
            severity="warning",
        )

    def on_show(self, _event: events.Show) -> None:
        self.focus()

    def on_focus(self, _event: events.Focus) -> None:
        self.review_view.active_pane = self
        self.app.run_worker(
            self.reload_in_place(),
            group=f"review-load-{self.file_path}",
            exclusive=True,
        )

    async def reload_in_place(self) -> None:
        # comment: loading a file tab starts empty, so refresh the diff in place and then restore the
        # visible cursor and scroll position after the diff text is ready.
        workspace = self.app.workspace  # type: ignore[attr-defined]
        cursor = self.cursor_location
        selection = self.selection
        scroll_x, scroll_y = self.scroll_offset
        self.diff = await asyncio.to_thread(get_diff, workspace / self.file_path)
        self.load_text(_diff_text(self.diff))
        if self.selection.is_empty:
            self.line_selection_anchor = None
            self.line_selection_cursor = None
        target_line = min(cursor[0], self.document.line_count - 1)
        target_column = min(cursor[1], len(self.document.get_line(target_line)))
        self.move_cursor((target_line, target_column))
        self.selection = selection
        self.scroll_to(scroll_x, scroll_y, animate=False, immediate=True)
        self.border_title = _comment_title(self)

    def render_line(self, y: int):
        strip = super().render_line(y)
        absolute_y = self.scroll_offset[1] + y
        if absolute_y >= len(self.diff):
            return strip

        line_type = self.diff[absolute_y]["type"]
        if self.show_line_numbers:
            content = strip.crop(self.gutter_width)
            gutter = self._gutter_strip(absolute_y)
            strip = Strip.join([gutter, content])

        if line_type != "-":
            return strip
        return strip.apply_style(Style(dim=True))

    def _gutter_strip(self, line_index: int) -> Strip:
        theme = self._theme
        if theme and self.cursor_location[0] == line_index:
            gutter_style = theme.cursor_line_gutter_style
        elif theme:
            gutter_style = theme.gutter_style
        else:
            gutter_style = self.rich_style

        line_number = self._display_line_number(line_index)
        gutter_width_no_margin = self.gutter_width - 2
        gutter_text = "" if line_number is None else str(line_number)
        symbol = _gutter_symbol(self, line_index)
        line_width = max(0, gutter_width_no_margin - 1)
        return Strip(
            [Segment(f"{symbol}{gutter_text:>{line_width}}  ", gutter_style)],
            self.gutter_width,
        )

    def _display_line_number(self, line_index: int) -> int | None:
        if self.diff[line_index]["type"] == "-":
            return None
        visible_lines = sum(
            1 for line in self.diff[: line_index + 1] if line["type"] != "-"
        )
        return self.line_number_start + visible_lines - 1

    def jump_to_file_line(self, line_number: int) -> None:
        if not self.diff:
            return
        target_line = _diff_line_for_file_line(self.diff, line_number)
        self.move_cursor((target_line, 0), record_width=False)
        if self.is_mounted:
            self.scroll_cursor_visible(animate=False)

    def _move_cursor_lines(self, delta: int) -> None:
        line, column = self.cursor_location
        target_line = max(0, min(line + delta, self.document.line_count - 1))
        if self.line_selection_anchor is None or self.line_selection_cursor is None:
            self.move_cursor((target_line, column), record_width=False)
            return
        self.line_selection_cursor = max(
            0,
            min(self.line_selection_cursor + delta, self.document.line_count - 1),
        )
        self.selection = _line_selection(
            self,
            self.line_selection_anchor,
            self.line_selection_cursor,
        )

    def action_review_cursor_down(self) -> None:
        self._move_cursor_lines(1)

    def action_review_cursor_up(self) -> None:
        self._move_cursor_lines(-1)

    def action_review_select_line(self) -> None:
        self.line_selection_anchor = self.cursor_location[0]
        self.line_selection_cursor = self.cursor_location[0]
        self.selection = _line_selection(
            self,
            self.line_selection_anchor,
            self.line_selection_anchor,
        )

    def action_review_scroll_home(self) -> None:
        self.move_cursor((0, 0), record_width=False)

    def action_review_scroll_end(self) -> None:
        self.move_cursor((self.document.line_count - 1, 0), record_width=False)

    def action_review_next_word(self) -> None:
        if location := next_word_location(self.text, self.cursor_location):
            self.move_cursor(location, record_width=False)

    def action_review_previous_word(self) -> None:
        if location := previous_word_location(self.text, self.cursor_location):
            self.move_cursor(location, record_width=False)

    def action_review_next_file_tab(self) -> None:
        if self._tab_switch_blocked():
            return
        self._cycle_file_tab(1)

    def action_review_previous_file_tab(self) -> None:
        if self._tab_switch_blocked():
            return
        self._cycle_file_tab(-1)

    def _tab_switch_blocked(self) -> bool:
        now = time.monotonic()
        # comment: holding Tab generates repeated key events, but file tabs should move one step at a time.
        if now - self.last_tab_switch_at < TAB_SWITCH_COOLDOWN:
            return True
        self.last_tab_switch_at = now
        return False

    def _cycle_file_tab(self, delta: int) -> None:
        tabs = self.screen.query_one("#review-tabs", TabbedContent)
        pane_ids = [
            pane.id
            for pane in tabs.query(TabPane)
            if pane.id is not None and pane.query(ReviewDiffView)
        ]
        if not pane_ids:
            return
        current_index = pane_ids.index(tabs.active) if tabs.active in pane_ids else 0
        next_id = pane_ids[(current_index + delta) % len(pane_ids)]
        tabs.active = next_id
        tabs.get_pane(next_id).query_one(ReviewDiffView).focus()

    def action_review_next_modification(self) -> None:
        if line := next_modification(self.diff, self.cursor_location[0]):
            self.move_cursor((line, 0), center=True, record_width=False)

    def action_review_previous_modification(self) -> None:
        if line := previous_modification(self.diff, self.cursor_location[0]):
            self.move_cursor((line, 0), center=True, record_width=False)

    def action_review_jump_next(self) -> None:
        if not self.review_view.search_term:
            return
        if location := next_search_location(
            self.diff,
            self.review_view.search_term,
            self.cursor_location,
            whole_word=self.review_view.search_whole_word,
        ):
            self.move_cursor(location, center=True, record_width=False)

    def action_review_jump_previous(self) -> None:
        if not self.review_view.search_term:
            return
        if location := previous_search_location(
            self.diff,
            self.review_view.search_term,
            self.cursor_location,
            whole_word=self.review_view.search_whole_word,
        ):
            self.move_cursor(location, center=True, record_width=False)

    async def action_review_refresh_current_file(self) -> None:
        await self.reload_in_place()

    async def action_review_search_word_under_cursor(self) -> None:
        await self.reload_in_place()
        term = word_under_cursor(
            self.diff[self.cursor_location[0]]["text"], self.cursor_location[1]
        )
        if term is None:
            self.app.notify("No word under cursor.", severity="warning")
            return
        self.review_view.search_term = term
        self.review_view.search_whole_word = True
        self.action_review_jump_next()

    async def action_review_search(self) -> None:
        await self.reload_in_place()

        def on_term(term: str | None) -> None:
            if term is None:
                return
            self.review_view.search_term = term
            self.review_view.search_whole_word = False
            self.action_review_jump_next()

        self.app.push_screen(
            SearchInFile(initial_term=self.review_view.search_term),
            on_term,
        )

    def action_review_escape(self) -> None:
        if self.line_selection_anchor is not None:
            self.selection = type(self.selection).cursor(self.cursor_location)
            self.line_selection_anchor = None
            self.line_selection_cursor = None
            return
        if not self.review_view.search_term:
            return
        self.review_view.search_term = ""
        self.review_view.search_whole_word = False

    async def action_review_add(self) -> None:
        await self.reload_in_place()
        start, end = _review_range(self)
        code = _get_code_for_review_submission(self.diff, start, end)
        existing = get_review(
            self.review_view.reviews,
            filename=self.file_path,
            line_number_start=start + 1,
            line_number_end=end + 1,
        )

        async def on_comment(comment: str | None) -> None:
            if comment is None:
                return
            self.review_view.add_review(
                {
                    "filename": self.file_path,
                    "line_number_start": start + 1,
                    "line_number_end": end + 1,
                    "code": code,
                    "comment": comment,
                }
            )
            await self.reload_in_place()

        self.app.push_screen(
            ReviewCommentModal(
                self.file_path,
                start + 1,
                end + 1,
                code,
                initial_comment="" if existing is None else existing["comment"],
            ),
            on_comment,
        )

    async def action_review_stage_lines(self) -> None:
        await self.reload_in_place()
        start, end = _review_range(self)
        workspace = self.app.workspace  # type: ignore[attr-defined]
        target = get_selected_change_state(
            self.diff, self.cursor_location[0], start, end
        )
        if target is None:
            self.app.notify(
                "No modified lines to stage or unstage here.", severity="warning"
            )
            return
        if error := apply_selected_diff_lines(
            self.diff,
            self.file_path,
            workspace,
            (start, end),
            is_staged=target,
        ):
            self.app.notify(error, severity="warning")
            return
        self.selection = type(self.selection).cursor(self.cursor_location)
        self.line_selection_anchor = None
        self.line_selection_cursor = None
        await self.reload_in_place()

    async def action_review_stage_file(self) -> None:
        workspace = self.app.workspace  # type: ignore[attr-defined]
        if error := await asyncio.to_thread(stage_file, workspace, self.file_path):
            self.app.notify(error, severity="warning")
            return
        self.selection = type(self.selection).cursor(self.cursor_location)
        self.line_selection_anchor = None
        self.line_selection_cursor = None
        await self.reload_in_place()

    async def action_review_submit_reviews(self) -> None:
        await self.review_view.submit_reviews()


def _register_extra_languages(view: ReviewDiffView) -> None:
    view.register_language(
        "lua",
        Language(tree_sitter_lua.language()),
        tree_sitter_lua.HIGHLIGHTS_QUERY,
    )
    highlight_query = tree_sitter_typescript.HIGHLIGHTS_QUERY
    view.register_language(
        "typescript",
        Language(tree_sitter_typescript.language_typescript()),
        highlight_query,
    )
    view.register_language(
        "tsx",
        Language(tree_sitter_typescript.language_tsx()),
        highlight_query,
    )


def _language_package(language: str) -> str:
    return {
        "c": "tree-sitter-c",
        "cpp": "tree-sitter-cpp",
        "lua": "tree-sitter-lua",
        "ruby": "tree-sitter-ruby",
        "tsx": "tree-sitter-typescript",
        "typescript": "tree-sitter-typescript",
    }.get(language, f"tree-sitter-{language}")


def _diff_text(diff: Diff) -> str:
    return "\n".join(line["text"] for line in diff)


def _line_selection(
    view: ReviewDiffView,
    anchor_line: int,
    current_line: int,
):
    selection_type = type(view.selection)
    if current_line < anchor_line:
        return selection_type((anchor_line + 1, 0), (current_line, 0))
    if current_line + 1 < view.document.line_count:
        return selection_type((anchor_line, 0), (current_line + 1, 0))
    return selection_type(
        (anchor_line, 0),
        (current_line, len(view.document.get_line(current_line))),
    )


def _review_range(view: ReviewDiffView) -> tuple[int, int]:
    start = view.selection.start[0]
    end = view.selection.end[0]
    if end < start:
        start, end = end, start
    # comment: Textual selections ending at column 0 point at the start of the next line.
    if view.selection.end[1] == 0 and end > start:
        end -= 1
    return start, end


def _get_code_for_review_submission(diff: Diff, start: int, end: int) -> str:
    return "\n".join(
        (
            f"-{line['text']}"
            if line["type"] == "-"
            else f"+{line['text']}"
            if line["type"] == "+"
            else line["text"]
        )
        for line in diff[start : end + 1]
    )


def _gutter_symbol(view: ReviewDiffView, line_index: int) -> str:
    if line_index in _commented_lines(view):
        return "*"
    line = view.diff[line_index]
    if line["is_staged"] and line["type"] in {"+", "-"}:
        return "|"
    return line["type"] or " "


def _comment_title(view: ReviewDiffView) -> str:
    count = sum(
        1 for review in view.review_view.reviews if review["filename"] == view.file_path
    )
    return f"{count} comment" if count == 1 else f"{count} comments"


def _commented_lines(view: ReviewDiffView) -> set[int]:
    lines: set[int] = set()
    for review in view.review_view.reviews:
        if review["filename"] != view.file_path:
            continue
        lines.update(range(review["line_number_start"] - 1, review["line_number_end"]))
    return lines


def _diff_line_for_file_line(diff: Diff, line_number: int) -> int:
    visible_line = max(1, line_number)
    current_line = 0
    for index, line in enumerate(diff):
        if line["type"] == "-":
            continue
        current_line += 1
        if current_line >= visible_line:
            return index
    return max(0, len(diff) - 1)
