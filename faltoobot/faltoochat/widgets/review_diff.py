import asyncio
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
from textual.message import Message
from textual.color import Color
from textual.strip import Strip
from textual.widgets import TextArea
from textual.widgets.text_area import TextAreaTheme


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
from ..git import apply_selected_diff_lines, stage_file
from ..review_api import FILE_COMMENT_LINE, get_review
from ..terminal import open_in_editor

from .review_comment_modal import ReviewCommentModal
from .text_input_modal import TextInputModal

if TYPE_CHECKING:
    from ..app import FaltooChatApp
    from ..review import ReviewView
    from .review_file import ReviewFileView

FULL_FILTER = "full"
ADDED_FILTER = "added"

LANGUAGES_BY_SUFFIX = {
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".css": "css",
    ".go": "go",
    ".h": "c",
    ".hpp": "cpp",
    ".html": "html",
    ".java": "java",
    ".js": "javascript",
    ".json": "json",
    ".jsx": "javascript",
    ".lua": "lua",
    ".md": "markdown",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".sh": "bash",
    ".sql": "sql",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".txt": None,
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
}


class DisplayRowContext(TypedDict):
    document_line: int
    diff_line: int
    line_type: str
    line_number: int | None
    symbol: str


class ReviewDiffView(TextArea):
    DEFAULT_CSS = """
    ReviewDiffView {
        border: round $panel;
        scrollbar-size-horizontal: 0;
        scrollbar-size-vertical: 0;
    }

    ReviewDiffView:focus {
        border: round $primary;
    }
    """

    class FocusedPane(Message):
        def __init__(self, viewer: "ReviewDiffView") -> None:
            super().__init__()
            self.viewer = viewer

    class FocusOtherPaneRequested(Message):
        pass

    class OpenSplitRequested(Message):
        def __init__(self, viewer: "ReviewDiffView") -> None:
            super().__init__()
            self.viewer = viewer

    class CloseSplitRequested(Message):
        pass

    class RefreshRequested(Message):
        def __init__(self, viewer: "ReviewDiffView") -> None:
            super().__init__()
            self.viewer = viewer

    class FileTabCycleRequested(Message):
        def __init__(self, delta: int) -> None:
            super().__init__()
            self.delta = delta

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
        Binding("m", "review_cycle_mode", "Diff View", priority=True, show=True),
        Binding("o", "review_focus_other_pane", "Other Pane", priority=True, show=True),
        Binding("O", "review_open_split", "Open Split", priority=True, show=True),
        Binding("q", "review_close_split", "Close Split", priority=True, show=True),
        Binding("a,c", "review_add", "Add Review", priority=True, show=True),
        Binding("C", "review_add_file", "Add File Review", priority=True, show=True),
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
        filter_mode: str = FULL_FILTER,
        file_view: "ReviewFileView",
        **kwargs,
    ) -> None:
        requested_language = LANGUAGES_BY_SUFFIX.get(file_path.suffix.lower())
        line_highlights = kwargs.pop("line_highlights", True)
        self.indent_guides = kwargs.pop("indent_guides", True)
        self.file_path = file_path
        self.review_view = review_view
        self.file_view = file_view
        self.diff = diff
        self.loaded = bool(diff)
        self.filter_mode = filter_mode
        self.visible_diff_lines: list[int] = []
        self._diff_line_by_visible_row: list[int] = []
        self._visible_row_by_diff_line: dict[int, int] = {}
        self.line_selection_anchor: int | None = None
        self.line_selection_cursor: int | None = None
        self.previous_cursor_locations: list[tuple[int, int]] = []
        self.missing_language_package: str | None = None
        kwargs.setdefault("soft_wrap", True)
        super().__init__("", language=None, **kwargs)
        self.line_highlights = line_highlights
        self.styles.scrollbar_size_horizontal = 0
        _use_review_theme(self, dark=False)
        self._load_diff_text()
        _register_extra_languages(self)
        if requested_language in self.available_languages:
            self.language = requested_language
        elif requested_language is not None:
            self.missing_language_package = _language_package(requested_language)

    def on_mount(self) -> None:
        super().on_mount()
        self.refresh_review_theme()
        if self.missing_language_package is None:
            # comment: all requested syntax highlighting support is already available.
            return
        self.app.notify(
            f"Install `{self.missing_language_package}` for {self.file_path.suffix} syntax highlighting.",
            severity="warning",
        )

    def refresh_review_theme(self) -> None:
        _use_review_theme(self, dark=self.app.current_theme.dark)
        self.refresh()

    @property
    def gutter_width(self) -> int:
        """Return the TextArea gutter width plus one column for the diff marker."""
        if not self.show_line_numbers:
            return 0
        return super().gutter_width + 1

    def on_focus(self, _event: events.Focus) -> None:
        self.review_view.active_pane = self
        self.post_message(self.FocusedPane(self))

    def render_line(self, y: int):
        highlight_cursor_line = self.highlight_cursor_line
        self.highlight_cursor_line = highlight_cursor_line and self.has_focus
        try:
            strip = super().render_line(y)
        finally:
            self.highlight_cursor_line = highlight_cursor_line
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
                guide_style=_indent_guide_style(self),
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

        if context["line_type"] == "-":
            strip = strip.apply_style(Style(dim=True))
        return strip

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
        diff_line = self.visible_diff_lines[document_line]
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
        self.show_diff_line(
            _diff_line_for_file_line(self.diff, line_number), center=True
        )

    def set_diff(self, diff: Diff) -> None:
        self.diff = diff
        self.loaded = True
        self._load_diff_text()
        self._update_border_title()

    async def reload_in_place(self) -> None:
        workspace = cast("FaltooChatApp", self.app).workspace
        diff_line = self.current_diff_line() if self.visible_diff_lines else 0
        top_diff_line = self.top_visible_diff_line() if self.visible_diff_lines else 0

        self.diff = await asyncio.to_thread(get_diff, workspace / self.file_path)
        self.loaded = True
        self._load_diff_text(preserve_cursor=False)
        if self._visible_row_by_diff_line:
            self.show_diff_line(diff_line)
            self.scroll_top_to_diff_line(top_diff_line)
        self.selection = type(self.selection).cursor(self.cursor_location)
        self.line_selection_anchor = None
        self.line_selection_cursor = None
        self._update_border_title()

    def set_filter_mode(
        self,
        filter_mode: str,
        *,
        force: bool = False,
        preserve_cursor: bool = True,
        center: bool = False,
    ) -> None:
        if self.filter_mode == filter_mode and not force:
            return
        self.filter_mode = filter_mode
        self._load_diff_text(center=center, preserve_cursor=preserve_cursor)

    def set_visible_diff_lines(self, visible_diff_lines: list[int]) -> None:
        self.visible_diff_lines = visible_diff_lines
        self._diff_line_by_visible_row = list(visible_diff_lines)
        self._visible_row_by_diff_line = {
            line: index for index, line in enumerate(visible_diff_lines)
        }
        self.load_text(
            "\n".join(self.diff[index]["text"] for index in visible_diff_lines)
        )
        self._update_border_title()

    def current_diff_line(self) -> int:
        return self._visible_diff_line(self.cursor_location[0])

    def show_diff_line(
        self,
        diff_line: int,
        *,
        column: int = 0,
        center: bool = False,
    ) -> None:
        self._jump_to_diff_line(diff_line, column=column, center=center)
        if center:
            if not self.is_mounted:
                return
            height = (
                self.file_view.content_size.height
                or max(0, self.file_view.size.height - 2)
                or max(0, self.app.size.height - 2)
                or max(0, self.screen.size.height - 2)
                or self.scrollable_content_region.size.height
                or self.size.height
            )
            self.scroll_to(
                self.scroll_offset[0],
                max(0, self._first_display_row(self.cursor_location[0]) - height // 2),
                animate=False,
                immediate=True,
                force=True,
            )
            return
        if self.is_mounted:
            self.scroll_cursor_visible(center=False, animate=False)

    def _jump_to_diff_line(
        self,
        diff_line: int,
        *,
        column: int = 0,
        center: bool = False,
    ) -> None:
        target_line = self._display_line(diff_line)
        target_column = min(column, len(self.document.get_line(target_line)))
        self._jump_cursor((target_line, target_column), center=center)

    def _first_display_row(self, document_line: int) -> int:
        offsets = self.wrapped_document._line_index_to_offsets
        if document_line < len(offsets) and offsets[document_line]:
            return offsets[document_line][0]
        return max(0, len(self.wrapped_document._offset_to_line_info) - 1)

    def top_visible_diff_line(self) -> int:
        context = self._display_row_context(int(self.scroll_offset[1]))
        if context is None:
            return self.current_diff_line()
        return self._visible_diff_line(context["document_line"])

    def scroll_top_to_diff_line(self, diff_line: int) -> None:
        target_line = self._display_line(diff_line)
        self.scroll_to(
            self.scroll_offset[0],
            self._first_display_row(target_line),
            animate=False,
            immediate=True,
        )

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

    def _update_border_title(self) -> None:
        self.border_title = comment_title(self)
        mode = "added" if self.filter_mode == ADDED_FILTER else "unified"
        self.border_subtitle = f"{self.file_path} · {mode}"

    def _visible_diff_line(self, line_index: int) -> int:
        line_index = max(0, min(line_index, len(self._diff_line_by_visible_row) - 1))
        return self._diff_line_by_visible_row[line_index]

    def _display_line(self, diff_line: int) -> int:
        if diff_line in self._visible_row_by_diff_line:
            return self._visible_row_by_diff_line[diff_line]
        for current in sorted(self._visible_row_by_diff_line):
            if current >= diff_line:
                return self._visible_row_by_diff_line[current]
        return len(self.visible_diff_lines) - 1

    def _load_diff_text(
        self, *, center: bool = False, preserve_cursor: bool = True
    ) -> None:
        diff_line = (
            self._visible_diff_line(self.cursor_location[0])
            if self.visible_diff_lines
            else 0
        )
        self.set_visible_diff_lines(visible_diff_lines(self.diff, self.filter_mode))
        if (
            not preserve_cursor
            or not self.visible_diff_lines
            or self.document.line_count == 0
        ):
            return
        self.move_cursor(
            (self._display_line(diff_line), 0),
            center=center and self.is_mounted,
            record_width=False,
        )

    def action_review_cycle_mode(self) -> None:
        self.set_filter_mode(
            ADDED_FILTER if self.filter_mode == FULL_FILTER else FULL_FILTER,
            preserve_cursor=True,
            center=True,
        )

    def action_review_focus_other_pane(self) -> None:
        self.post_message(self.FocusOtherPaneRequested())

    def action_review_open_split(self) -> None:
        self.post_message(self.OpenSplitRequested(self))

    def action_review_close_split(self) -> None:
        self.post_message(self.CloseSplitRequested())

    def action_review_toggle_wrap(self) -> None:
        self.app.notify("Line wrapping is always enabled.")

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

    def action_review_next_file_tab(self) -> None:
        self.post_message(self.FileTabCycleRequested(1))

    def action_review_previous_file_tab(self) -> None:
        self.post_message(self.FileTabCycleRequested(-1))

    def action_review_next_modification(self) -> None:
        target_line = next_modification(self.diff, self.current_diff_line())
        if target_line is not None:
            self.show_diff_line(target_line, center=True)

    def action_review_previous_modification(self) -> None:
        target_line = previous_modification(self.diff, self.current_diff_line())
        if target_line is not None:
            self.show_diff_line(target_line, center=True)

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

    def action_review_refresh_current_file(self) -> None:
        self.post_message(self.RefreshRequested(self))

    async def action_review_edit_file(self) -> None:
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
        await self.file_view.reload_in_place()
        self.jump_to_file_line(line_number)

    async def action_review_search_word_under_cursor(self) -> None:
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
        def on_term(term: str | None) -> None:
            if term is None:
                return
            self.review_view.search_term = term
            self.review_view.search_whole_word = False
            self.action_review_jump_next()

        self.app.push_screen(
            TextInputModal(
                initial_value=self.review_view.search_term,
                title=f"Search {self.file_path.name}",
            ),
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
        start, end = _review_range(self)
        await self._add_review(start, end)

    async def action_review_add_file(self) -> None:
        # comment: deleted/empty tabs can briefly have no diff rows to attach a file comment to.
        if not self.diff:
            return
        await self._add_review(0, len(self.diff) - 1, file_comment=True)

    async def _add_review(
        self, start: int, end: int, *, file_comment: bool = False
    ) -> None:
        code = (
            ""
            if file_comment
            else _get_code_for_review_submission(self.diff, start, end)
        )
        file_line_number_start = (
            FILE_COMMENT_LINE
            if file_comment
            else _file_line_for_diff_line(self.diff, start)
        )
        file_line_number_end = (
            FILE_COMMENT_LINE
            if file_comment
            else _file_line_for_diff_line(self.diff, end)
        )
        line_number_start = FILE_COMMENT_LINE if file_comment else start + 1
        line_number_end = FILE_COMMENT_LINE if file_comment else end + 1
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
            self._update_border_title()
            self.refresh()

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
        selected_diff_lines = {
            self.visible_diff_lines[row]
            for row in _selected_visible_rows(self)
            if 0 <= row < len(self.visible_diff_lines)
        }
        states = {
            self.diff[index]["is_staged"]
            for index in selected_diff_lines
            if self.diff[index]["type"] in {"+", "-"}
        }
        if not states:
            self.app.notify(
                "No modified lines to stage or unstage here.", severity="warning"
            )
            return
        target = False if False in states else True
        workspace = cast("FaltooChatApp", self.app).workspace
        if error := apply_selected_diff_lines(
            self.diff,
            self.file_path,
            workspace,
            selected_diff_lines,
            is_staged=target,
        ):
            self.app.notify(error, severity="warning")
            return
        self.selection = type(self.selection).cursor(self.cursor_location)
        self.line_selection_anchor = None
        self.line_selection_cursor = None
        await self.reload_in_place()

    async def action_review_stage_file(self) -> None:
        workspace = cast("FaltooChatApp", self.app).workspace
        if error := await asyncio.to_thread(stage_file, workspace, self.file_path):
            self.app.notify(error, severity="warning")
            return
        self.selection = type(self.selection).cursor(self.cursor_location)
        self.line_selection_anchor = None
        self.line_selection_cursor = None
        await self.review_view.refresh_files()

    async def action_review_submit_reviews(self) -> None:
        await self.review_view.submit_reviews()


def _use_review_theme(view: ReviewDiffView, *, dark: bool) -> None:
    """Use syntax colors for the app theme without forcing TextArea's background."""
    theme_name = "vscode_dark" if dark else "github_light"
    review_theme_name = f"faltoobot_review_{'dark' if dark else 'light'}"
    theme = TextAreaTheme.get_builtin_theme(theme_name)
    if theme is None:
        # comment: fall back to TextArea's default theme if Textual changes built-in names.
        return
    view.register_theme(
        TextAreaTheme(
            name=review_theme_name,
            cursor_line_style=theme.cursor_line_style,
            cursor_line_gutter_style=theme.cursor_line_gutter_style,
            syntax_styles=theme.syntax_styles,
        )
    )
    view.theme = review_theme_name


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


def visible_diff_lines(diff: Diff, filter_mode: str) -> list[int]:
    """Return backing diff line indexes visible in the current filter."""
    if filter_mode == ADDED_FILTER:
        return [index for index, line in enumerate(diff) if line["type"] != "-"]
    return list(range(len(diff)))


def _leading_spaces(text: str, indent_width: int) -> int:
    """Return leading indentation after expanding tabs to spaces."""
    expanded = text.expandtabs(indent_width)
    return len(expanded) - len(expanded.lstrip(" "))


def _guide_columns(
    text: str,
    *,
    indent_width: int,
    scroll_x: int,
    line_width: int,
) -> set[int]:
    """Return visible columns where indent guides should be drawn."""
    if indent_width <= 0:
        return set()
    spaces = _leading_spaces(text, indent_width)
    if spaces < indent_width:
        return set()
    return {
        column - scroll_x
        for column in range(0, spaces, indent_width)
        if 0 <= column - scroll_x < line_width
    }


def _apply_indent_guides(
    strip: Strip,
    text: str,
    *,
    indent_width: int,
    guide_style: Style,
    scroll_x: int = 0,
) -> Strip:
    """Overlay indent guides without mutating the TextArea text."""
    guide_columns = _guide_columns(
        text,
        indent_width=indent_width,
        scroll_x=scroll_x,
        line_width=strip.cell_length,
    )
    if not guide_columns:
        return strip

    segments: list[Segment] = []
    cell = 0
    for segment in strip._segments:
        if segment.control:
            segments.append(segment)
            continue
        style = segment.style
        for char in segment.text:
            char_style = style
            if cell in guide_columns and char == " ":
                char_style = (Style() if style is None else style) + guide_style
                char = "│"
            segments.append(Segment(char, char_style))
            cell += 1
    return Strip(segments, strip.cell_length)


def _indent_guide_style(view: ReviewDiffView) -> Style:
    theme = view.app.current_theme
    panel = theme.panel or theme.surface or theme.foreground or "#808080"
    try:
        color = Color.parse(panel).darken(0.15)
    except Exception:
        # comment: ANSI/custom themes can expose colors that Rich cannot parse here.
        return Style(dim=True)
    return Style(color=color.rich_color, dim=True)


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
        and view.has_focus
        and view.cursor_location[0] == document_line
    ):
        return theme.cursor_line_style or view.rich_style
    if theme and theme.base_style is not None:
        return theme.base_style
    return view.rich_style


def _gutter_base_style(view: ReviewDiffView, document_line: int) -> Style:
    theme = view._theme
    if theme and view.has_focus and view.cursor_location[0] == document_line:
        return theme.cursor_line_gutter_style or view.rich_style
    if theme:
        return theme.gutter_style or view.rich_style
    return view.rich_style


def _line_end(view: ReviewDiffView, line: int) -> int:
    return len(view.document.get_line(line))


def _line_selection(
    view: ReviewDiffView,
    anchor_line: int,
    current_line: int,
):
    selection_type = type(view.selection)
    if current_line < anchor_line:
        # comment: reverse selections must start after the anchor to cover full lines.
        return selection_type(
            (anchor_line, _line_end(view, anchor_line)), (current_line, 0)
        )
    return selection_type(
        (anchor_line, 0), (current_line, _line_end(view, current_line))
    )


def _selected_visible_rows(view: ReviewDiffView) -> set[int]:
    if (
        view.line_selection_anchor is not None
        and view.line_selection_cursor is not None
    ):
        start, end = sorted((view.line_selection_anchor, view.line_selection_cursor))
    elif view.selection.is_empty:
        start = end = view.cursor_location[0]
    else:
        start, end = sorted((view.selection.start[0], view.selection.end[0]))
    return set(range(start, end + 1))


def _review_range(view: ReviewDiffView) -> tuple[int, int]:
    """Return inclusive backing diff-line range for text or visual-line selection."""
    if (
        view.line_selection_anchor is not None
        and view.line_selection_cursor is not None
    ):
        # comment: visual line mode tracks exact rows even when selection endpoints are line ends.
        start = min(view.line_selection_anchor, view.line_selection_cursor)
        end = max(view.line_selection_anchor, view.line_selection_cursor)
    else:
        start = view.selection.start[0]
        end = view.selection.end[0]
        if end < start:
            start, end = end, start
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


def comment_title(view: ReviewDiffView) -> str:
    count = sum(
        1 for review in view.review_view.reviews if review["filename"] == view.file_path
    )
    comments = f"{count} comment" if count == 1 else f"{count} comments"
    staged, total = _hunk_counts(view.diff)
    hunks = f"{staged}/{total} hunks staged"
    return f"{comments} · {hunks}"


def _hunk_counts(diff: Diff) -> tuple[int, int]:
    total = 0
    staged = 0
    in_hunk = False
    hunk_staged = False
    for line in diff:
        if line["type"] not in {"+", "-"}:
            if in_hunk:
                staged += int(hunk_staged)
            in_hunk = False
            hunk_staged = False
            continue
        if not in_hunk:
            total += 1
            in_hunk = True
        hunk_staged = hunk_staged or line["is_staged"]
    if in_hunk:
        staged += int(hunk_staged)
    return staged, total


def _commented_lines(view: ReviewDiffView) -> set[int]:
    lines: set[int] = set()
    for review in view.review_view.reviews:
        if review["filename"] != view.file_path:
            continue
        if (
            review["line_number_start"] == FILE_COMMENT_LINE
            and review["line_number_end"] == FILE_COMMENT_LINE
        ):
            # comment: file-level comments belong in the title count, not every line gutter.
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
