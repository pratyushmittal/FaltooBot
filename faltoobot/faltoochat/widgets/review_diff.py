import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast

from rich.segment import Segment
from rich.style import Style
from tree_sitter import Language
import tree_sitter_lua
import tree_sitter_typescript
from textual import events
from textual.app import SuspendNotSupported
from textual.binding import Binding
from textual.color import Color
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
from ..terminal import open_in_editor

from .review_comment_modal import ReviewCommentModal
from .text_input_modal import TextInputModal

if TYPE_CHECKING:
    from ..app import FaltooChatApp
    from ..review import ReviewView

TAB_SWITCH_COOLDOWN = 0.2
DIFF_MODE = "diff"
ADD_MODE = "add"


class DisplayRowContext(TypedDict):
    document_line: int
    diff_line: int
    line_type: str
    line_number: int | None
    symbol: str


class ReviewDiffView(TextArea):
    DEFAULT_BINDINGS = [
        Binding(
            "j,ctrl+n", "review_cursor_down", "Cursor Down", priority=True, show=False
        ),
        Binding("k,ctrl+p", "review_cursor_up", "Cursor Up", priority=True, show=False),
        Binding("h", "cursor_left", "Cursor Left", priority=True, show=False),
        Binding("l", "cursor_right", "Cursor Right", priority=True, show=False),
        Binding("g", "review_scroll_home", "Scroll to Top", priority=True, show=False),
        Binding(
            "G", "review_scroll_end", "Scroll to Bottom", priority=True, show=False
        ),
        Binding("w", "review_next_word", "Next Word", priority=True, show=False),
        Binding(
            "b", "review_previous_word", "Previous Word", priority=True, show=False
        ),
        Binding("ctrl+f", "review_page_down", "Page Down", priority=True, show=True),
        Binding("ctrl+b", "review_page_up", "Page Up", priority=True, show=True),
        Binding(
            "ctrl+o",
            "review_previous_cursor_position",
            "Previous Cursor",
            priority=True,
            show=True,
        ),
        Binding("tab", "review_next_file_tab", "Next File", priority=True, show=True),
        Binding(
            "shift+tab",
            "review_previous_file_tab",
            "Previous File",
            priority=True,
            show=False,
        ),
        Binding(
            "r", "review_refresh_current_file", "Refresh File", priority=True, show=True
        ),
        Binding(
            "]", "review_next_modification", "Next Change", priority=True, show=True
        ),
        Binding(
            "[",
            "review_previous_modification",
            "Previous Change",
            priority=True,
            show=True,
        ),
        Binding("V", "review_select_line", "Select Line", priority=True, show=True),
        Binding("W", "review_toggle_wrap", "Toggle Wrap", priority=True, show=True),
        Binding("ctrl+d", "review_edit_file", "Edit", priority=True, show=True),
        Binding(
            "H",
            "review_toggle_line_highlights",
            "Line Highlights",
            priority=True,
            show=True,
        ),
        Binding("n", "review_jump_next", "Next Match", priority=True, show=True),
        Binding(
            "N", "review_jump_previous", "Previous Match", priority=True, show=True
        ),
        Binding(
            "*",
            "review_search_word_under_cursor",
            "Search Word",
            priority=True,
            show=False,
        ),
        Binding("slash", "review_search", "Search File", priority=True, show=True),
        Binding("escape", "review_escape", "Exit Search", priority=True, show=True),
        Binding("m", "review_cycle_mode", "Review Mode", priority=True, show=True),
        Binding("a,c", "review_add", "Add Review", priority=True, show=True),
        Binding("s", "review_stage_lines", "Stage Lines", priority=True, show=True),
        Binding("S", "review_stage_file", "Stage File", priority=True, show=True),
        Binding(
            "shift+enter",
            "review_submit_reviews",
            "Submit Reviews",
            priority=True,
            show=True,
        ),
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
        line_highlights = kwargs.pop("line_highlights", True)
        indent_guides = kwargs.pop("indent_guides", True)
        self.file_path = file_path
        self.review_view = review_view
        self.diff = diff
        self.mode = DIFF_MODE
        self.visible_diff_lines: list[int] = []
        self.last_tab_switch_at = 0.0
        self.line_highlights = line_highlights
        self.indent_guides = indent_guides
        self.line_selection_anchor: int | None = None
        self.line_selection_cursor: int | None = None
        self.previous_cursor_locations: list[tuple[int, int]] = []
        self.missing_language_package: str | None = None
        kwargs.setdefault("soft_wrap", True)
        super().__init__("", language=None, **kwargs)
        self._load_diff_text()
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
        if await self.review_view.close_stale_file(self.file_path):
            return
        workspace = cast("FaltooChatApp", self.app).workspace
        cursor = self.cursor_location
        selection = self.selection
        scroll_x, scroll_y = self.scroll_offset
        self.diff = await asyncio.to_thread(get_diff, workspace / self.file_path)
        self._load_diff_text()
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
        if (context := self._display_row_context(absolute_y)) is None:
            return strip
        base_style = _content_base_style(self, context["document_line"])
        highlight = _line_highlight_style(
            self,
            context["diff_line"],
            base_style=base_style,
        )
        strip = _apply_line_highlight(
            strip.crop(self.gutter_width),
            highlight,
            base_background=_style_background(base_style),
        )
        line_info = self._display_line_info(absolute_y)
        if self.indent_guides and line_info is not None and line_info[1] == 0:
            strip = _apply_indent_guides(
                strip,
                self.diff[context["diff_line"]]["text"],
                indent_width=self.indent_width,
                scroll_x=0 if self.soft_wrap else int(self.scroll_offset[0]),
            )
        if self.show_line_numbers:
            gutter_style = _gutter_base_style(self, context["document_line"])
            gutter = _apply_line_highlight(
                self._gutter_strip(context),
                highlight,
                base_background=_style_background(gutter_style),
            )
            strip = Strip.join([gutter, strip])

        if context["line_type"] != "-":
            return strip
        return strip.apply_style(Style(dim=True))

    def _display_line_info(self, line_index: int) -> tuple[int, int] | None:
        try:
            return self.wrapped_document._offset_to_line_info[line_index]
        except IndexError:
            return None

    def _display_row_context(self, line_index: int) -> DisplayRowContext | None:
        line_info = self._display_line_info(line_index)
        if line_info is None or line_info[0] >= len(self.visible_diff_lines):
            return None
        document_line, section_offset = line_info
        diff_line = self._visible_diff_line(document_line)
        line_type = self.diff[diff_line]["type"]
        line_number = None
        if not section_offset and line_type != "-":
            line_number = (
                self.line_number_start
                + _file_line_for_diff_line(self.diff, diff_line)
                - 1
            )
        return {
            "document_line": document_line,
            "diff_line": diff_line,
            "line_type": line_type,
            "line_number": line_number,
            "symbol": _gutter_symbol(self, diff_line),
        }

    def _gutter_strip(self, context: DisplayRowContext) -> Strip:
        gutter_style = _gutter_base_style(self, context["document_line"])
        gutter_width_no_margin = self.gutter_width - 2
        gutter_text = (
            "" if context["line_number"] is None else str(context["line_number"])
        )
        line_width = max(0, gutter_width_no_margin - 1)
        return Strip(
            [
                Segment(
                    f"{context['symbol']}{gutter_text:>{line_width}}  ",
                    gutter_style,
                )
            ],
            self.gutter_width,
        )

    def _record_cursor_jump(self) -> None:
        location = self.cursor_location
        # comment: repeated jumps to the same place should not fill the jump-back stack.
        if (
            self.previous_cursor_locations
            and self.previous_cursor_locations[-1] == location
        ):
            return
        self.previous_cursor_locations.append(location)

    def _jump_cursor(
        self,
        location: tuple[int, int],
        *,
        center: bool = True,
    ) -> None:
        # comment: no-op jumps should not add duplicate entries to cursor history.
        if location == self.cursor_location:
            return
        self._record_cursor_jump()
        # comment: centering needs a mounted Textual app; tests can exercise jumps off-app.
        self.move_cursor(
            location, center=center and self.is_mounted, record_width=False
        )

    def jump_to_file_line(self, line_number: int) -> None:
        if not self.diff:
            return
        target_line = self._display_line(
            _diff_line_for_file_line(self.diff, line_number)
        )
        self._jump_cursor((target_line, 0))
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

    def action_review_page_down(self) -> None:
        self._record_cursor_jump()
        self.action_cursor_page_down()

    def action_review_page_up(self) -> None:
        self._record_cursor_jump()
        self.action_cursor_page_up()

    def action_review_previous_cursor_position(self) -> None:
        # comment: Ctrl+O is a no-op until a cursor jump records a previous location.
        if not self.previous_cursor_locations:
            return
        # comment: an empty diff has no valid document row to restore.
        if self.document.line_count == 0:
            return
        line, column = self.previous_cursor_locations.pop()
        # comment: the diff may have reloaded since the jump was recorded, so clamp the old location.
        target_line = max(0, min(line, self.document.line_count - 1))
        target_column = min(column, len(self.document.get_line(target_line)))
        self.move_cursor(
            (target_line, target_column), center=self.is_mounted, record_width=False
        )

    def action_review_select_line(self) -> None:
        self.line_selection_anchor = self.cursor_location[0]
        self.line_selection_cursor = self.cursor_location[0]
        self.selection = _line_selection(
            self,
            self.line_selection_anchor,
            self.line_selection_anchor,
        )

    def _update_mode_subtitle(self) -> None:
        self.border_subtitle = "" if self.mode == DIFF_MODE else self.mode

    def _visible_diff_line(self, line_index: int) -> int:
        """Return the backing diff line for a document row."""
        if not self.visible_diff_lines:
            return 0
        line_index = max(0, min(line_index, len(self.visible_diff_lines) - 1))
        return self.visible_diff_lines[line_index]

    def _display_line(self, diff_line: int) -> int:
        """Return the visible editor row for a backing diff line."""
        if not self.visible_diff_lines:
            return 0
        for line_index, current in enumerate(self.visible_diff_lines):
            if current >= diff_line:
                return line_index
        return len(self.visible_diff_lines) - 1

    def _load_diff_text(self, *, center: bool = False) -> None:
        diff_line = (
            self._visible_diff_line(self.cursor_location[0])
            if self.visible_diff_lines
            else 0
        )
        self.visible_diff_lines = _visible_diff_lines(self.diff, self.mode)
        self._update_mode_subtitle()
        self.load_text(_diff_text(self.diff, self.visible_diff_lines))
        if self.document.line_count == 0:
            return
        self.move_cursor(
            (self._display_line(diff_line), 0),
            center=center and self.is_mounted,
            record_width=False,
        )

    def action_review_cycle_mode(self) -> None:
        self.mode = ADD_MODE if self.mode == DIFF_MODE else DIFF_MODE
        self._load_diff_text(center=True)

    def action_review_toggle_line_highlights(self) -> None:
        self.review_view.set_display_preferences(
            line_highlights=not self.review_view.line_highlights,
        )

    def action_review_scroll_home(self) -> None:
        self._jump_cursor((0, 0), center=False)

    def action_review_scroll_end(self) -> None:
        self._jump_cursor((max(0, self.document.line_count - 1), 0), center=False)

    def action_review_next_word(self) -> None:
        if location := next_word_location(self.text, self.cursor_location):
            self._jump_cursor(location, center=False)

    def action_review_previous_word(self) -> None:
        if location := previous_word_location(self.text, self.cursor_location):
            self._jump_cursor(location, center=False)

    async def action_review_next_file_tab(self) -> None:
        if self._tab_switch_blocked():
            return
        await self._cycle_file_tab(1)

    async def action_review_previous_file_tab(self) -> None:
        if self._tab_switch_blocked():
            return
        await self._cycle_file_tab(-1)

    def _tab_switch_blocked(self) -> bool:
        now = time.monotonic()
        # comment: holding Tab generates repeated key events, but file tabs should move one step at a time.
        if now - self.last_tab_switch_at < TAB_SWITCH_COOLDOWN:
            return True
        self.last_tab_switch_at = now
        return False

    async def _cycle_file_tab(self, delta: int) -> None:
        tabs = self.screen.query_one("#review-tabs", TabbedContent)
        pane_ids = [
            pane.id
            for pane in tabs.query(TabPane)
            if pane.id is not None and pane.query(ReviewDiffView)
        ]
        while pane_ids:
            current_index = (
                pane_ids.index(tabs.active) if tabs.active in pane_ids else 0
            )
            next_id = pane_ids[(current_index + delta) % len(pane_ids)]
            viewer = tabs.get_pane(next_id).query_one(ReviewDiffView)
            if not await self.review_view.close_stale_file(viewer.file_path):
                tabs.active = next_id
                viewer.focus()
                return
            pane_ids = [
                pane.id
                for pane in tabs.query(TabPane)
                if pane.id is not None and pane.query(ReviewDiffView)
            ]

    def action_review_next_modification(self) -> None:
        line = next_modification(
            self.diff, self._visible_diff_line(self.cursor_location[0])
        )
        if line is None:
            return
        target = self._display_line(line)
        self._jump_cursor((target, 0), center=True)

    def action_review_previous_modification(self) -> None:
        line = previous_modification(
            self.diff, self._visible_diff_line(self.cursor_location[0])
        )
        if line is None:
            return
        target = self._display_line(line)
        self._jump_cursor((target, 0), center=True)

    def action_review_jump_next(self) -> None:
        if not self.review_view.search_term:
            return
        visible_diff = [self.diff[index] for index in self.visible_diff_lines]
        # comment: an empty diff has no visible rows to search.
        if not visible_diff:
            return
        if location := next_search_location(
            visible_diff,
            self.review_view.search_term,
            self.cursor_location,
            whole_word=self.review_view.search_whole_word,
        ):
            self._jump_cursor(location, center=True)

    def action_review_jump_previous(self) -> None:
        if not self.review_view.search_term:
            return
        visible_diff = [self.diff[index] for index in self.visible_diff_lines]
        # comment: an empty diff has no visible rows to search.
        if not visible_diff:
            return
        if location := previous_search_location(
            visible_diff,
            self.review_view.search_term,
            self.cursor_location,
            whole_word=self.review_view.search_whole_word,
        ):
            self._jump_cursor(location, center=True)

    async def action_review_refresh_current_file(self) -> None:
        await self.reload_in_place()

    async def action_review_edit_file(self) -> None:
        await self.reload_in_place()
        workspace = cast("FaltooChatApp", self.app).workspace
        line_number = _file_line_for_diff_line(
            self.diff,
            self._visible_diff_line(self.cursor_location[0]),
        )
        try:
            with self.app.suspend():
                used_terminal_editor = open_in_editor(
                    workspace / self.file_path,
                    line_number=line_number,
                )
        except SuspendNotSupported:
            used_terminal_editor = open_in_editor(
                workspace / self.file_path,
                line_number=line_number,
            )
        if not used_terminal_editor:
            return
        await self.review_view.refresh_files()
        await self.reload_in_place()
        self.jump_to_file_line(line_number)

    async def action_review_search_word_under_cursor(self) -> None:
        await self.reload_in_place()
        term = word_under_cursor(
            self.diff[self._visible_diff_line(self.cursor_location[0])]["text"],
            self.cursor_location[1],
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
            TextInputModal(initial_value=self.review_view.search_term),
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
        file_line_number_start = _file_line_for_diff_line(self.diff, start)
        file_line_number_end = _file_line_for_diff_line(self.diff, end)
        line_number_start = start + 1
        line_number_end = end + 1
        existing = get_review(
            self.review_view.reviews,
            filename=self.file_path,
            line_number_start=line_number_start,
            line_number_end=line_number_end,
        )

        async def on_comment(comment: str | None) -> None:
            if comment is None:
                return
            self.review_view.add_review(
                {
                    "filename": self.file_path,
                    "line_number_start": line_number_start,
                    "line_number_end": line_number_end,
                    "file_line_number_start": file_line_number_start,
                    "file_line_number_end": file_line_number_end,
                    "code": code,
                    "comment": comment,
                }
            )
            await self.reload_in_place()

        self.app.push_screen(
            ReviewCommentModal(
                self.file_path,
                file_line_number_start,
                file_line_number_end,
                code,
                initial_comment="" if existing is None else existing["comment"],
            ),
            on_comment,
        )

    async def action_review_stage_lines(self) -> None:
        await self.reload_in_place()
        start, end = _review_range(self)
        workspace = cast("FaltooChatApp", self.app).workspace
        target = get_selected_change_state(
            self.diff, self._visible_diff_line(self.cursor_location[0]), start, end
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

    def _next_review_file_path(self) -> Path | None:
        tabs = self.screen.query_one("#review-tabs", TabbedContent)
        file_paths = [
            pane.query_one(ReviewDiffView).file_path
            for pane in tabs.query(TabPane)
            if pane.id is not None and pane.query(ReviewDiffView)
        ]
        if self.file_path not in file_paths:
            return None
        current_index = file_paths.index(self.file_path)
        if current_index + 1 < len(file_paths):
            return file_paths[current_index + 1]
        if current_index > 0:
            return file_paths[current_index - 1]
        return None

    async def action_review_stage_file(self) -> None:
        next_path = self._next_review_file_path()
        workspace = cast("FaltooChatApp", self.app).workspace
        if error := await asyncio.to_thread(stage_file, workspace, self.file_path):
            self.app.notify(error, severity="warning")
            return
        self.selection = type(self.selection).cursor(self.cursor_location)
        self.line_selection_anchor = None
        self.line_selection_cursor = None
        # comment: staging the whole file usually removes it from the unstaged review list, so
        # refresh and close tabs that no longer belong in review.
        # comment: whole-file staging removes the current tab, so move the review view's
        # active pane to the neighbor first and let refresh_files preserve it.
        if next_path is not None:
            self.review_view.set_active_tab(next_path)
        await self.review_view.refresh_files(close_unmodified=True)

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


def _visible_diff_lines(diff: Diff, mode: str) -> list[int]:
    """Return backing diff line indexes for rows visible in the current mode."""
    if mode == ADD_MODE:
        return [index for index, line in enumerate(diff) if line["type"] != "-"]
    return list(range(len(diff)))


def _diff_text(diff: Diff, visible_diff_lines: list[int]) -> str:
    return "\n".join(diff[index]["text"] for index in visible_diff_lines)


def _apply_indent_guides(
    strip: Strip,
    text: str,
    *,
    indent_width: int,
    scroll_x: int = 0,
) -> Strip:
    """Overlay Rich-style indent guides without mutating TextArea text.

    The source text is expanded only to measure leading indentation. Every
    indent-width column inside that leading whitespace becomes a visible guide
    in the rendered strip, adjusted for horizontal scroll. Only existing blank
    cells are replaced, so cursor/search/staging positions still refer to the
    original document text.
    """
    if indent_width <= 0:
        return strip
    expanded = text.expandtabs(indent_width)
    leading_spaces = len(expanded) - len(expanded.lstrip(" "))
    if leading_spaces < indent_width:
        return strip
    # comment: find guide positions in the visible part of the rendered line.
    guide_columns = {
        column - scroll_x for column in range(0, leading_spaces, indent_width)
    }
    guide_columns = {
        column for column in guide_columns if 0 <= column < strip.cell_length
    }
    if not guide_columns:
        return strip

    guide_style = Style(dim=True)
    segments: list[Segment] = []
    cell = 0
    # comment: walk the rendered segments cell-by-cell, preserving existing styles and
    # replacing only blank cells that line up with an indent guide column.
    for segment in strip._segments:
        if segment.control:
            segments.append(segment)
            continue
        style = segment.style
        chars: list[Segment] = []
        for char in segment.text:
            next_style = style
            if cell in guide_columns and char == " ":
                char = "│"
                next_style = (Style() if style is None else style) + guide_style
            chars.append(Segment(char, next_style))
            cell += 1
        segments.extend(chars)
    return Strip(segments, strip.cell_length)


def _apply_line_highlight(
    strip: Strip,
    style: Style,
    *,
    base_background: Color | None,
) -> Strip:
    if style.bgcolor is None:
        return strip
    segments = []
    for segment in strip._segments:
        current = Style() if segment.style is None else segment.style
        background = (
            None if current.bgcolor is None else Color.from_rich_color(current.bgcolor)
        )
        if segment.control or background != base_background:
            segments.append(segment)
            continue
        segments.append(
            Segment(
                segment.text, current + Style(bgcolor=style.bgcolor), segment.control
            )
        )
    return Strip(segments, strip.cell_length)


def _content_base_style(view: ReviewDiffView, document_line: int) -> Style:
    theme = view._theme
    if (
        theme
        and view.highlight_cursor_line
        and view.cursor_location[0] == document_line
    ):
        return theme.cursor_line_style or view.rich_style
    if theme and theme.base_style is not None:
        return theme.base_style
    return view.rich_style


def _gutter_base_style(view: ReviewDiffView, document_line: int) -> Style:
    theme = view._theme
    if theme and view.cursor_location[0] == document_line:
        return theme.cursor_line_gutter_style or view.rich_style
    if theme:
        return theme.gutter_style or view.rich_style
    return view.rich_style


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
    # comment: line-mode selections use column-0 endpoints as a sentinel for the
    # next full line, but arbitrary text selections ending at the start of a later
    # line should still include that line in the review range.
    if (
        view.line_selection_anchor is not None
        and view.selection.end[1] == 0
        and end > start
    ):
        end -= 1
    return (
        view._visible_diff_line(start),
        view._visible_diff_line(end),
    )


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


def _style_background(style: Style) -> Color | None:
    return None if style.bgcolor is None else Color.from_rich_color(style.bgcolor)


def _get_target_color(
    view: ReviewDiffView,
    diff_line: int,
    *,
    base: Color,
) -> Color | None:
    theme = view.app.current_theme
    shift = theme.luminosity_spread * 2
    blending = 0.25
    if diff_line in _commented_lines(view):
        target = Color.parse(theme.primary).lighten(shift)
    else:
        line = view.diff[diff_line]
        if line["is_staged"] and line["type"] in {"+", "-"}:
            staged = theme.secondary or theme.primary
            target = Color.parse(staged).lighten(shift)
            blending = 0.18
        elif line["type"] == "-":
            target = Color.parse(theme.error).lighten(shift)
        elif line["type"] == "+":
            target = Color.parse(theme.success).lighten(shift)
        else:
            return None
    return base.blend(target, blending)


def _line_highlight_style(
    view: ReviewDiffView,
    diff_line: int,
    *,
    base_style: Style | None = None,
) -> Style:
    if not view.line_highlights:
        return Style()
    base = (
        _style_background(base_style or Style())
        or _style_background(view.rich_style)
        or Color.parse("#232323")
    )
    if (target := _get_target_color(view, diff_line, base=base)) is None:
        return Style()
    return Style(bgcolor=target.rich_color)


def _gutter_symbol(view: ReviewDiffView, diff_line: int) -> str:
    if diff_line in _commented_lines(view):
        return "*"
    line = view.diff[diff_line]
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
    """Return the diff row that displays the given 1-based file line."""
    visible_line = max(1, line_number)
    current_line = 0
    for index, line in enumerate(diff):
        if line["type"] == "-":
            continue
        current_line += 1
        if current_line >= visible_line:
            return index
    return max(0, len(diff) - 1)


def _file_line_for_diff_line(diff: Diff, diff_line: int) -> int:
    """Return the 1-based file line that best matches a diff row.

    Added and context rows map to their own file line. Deleted rows map to the
    next surviving file line so cursor-based actions still land on a valid file
    position.
    """
    line_number = sum(1 for line in diff[: diff_line + 1] if line["type"] != "-")
    if diff and diff[min(diff_line, len(diff) - 1)]["type"] == "-":
        total_lines = max(1, sum(1 for line in diff if line["type"] != "-"))
        return min(total_lines, line_number + 1)
    return max(1, line_number)
