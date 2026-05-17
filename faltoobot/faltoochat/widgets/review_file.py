import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.document._wrapped_document import compute_wrap_offsets
from textual.dom import NoScreen
from textual.widgets import Static

from ..diff import Diff, get_diff
from ..editor_utils import next_modification, previous_modification
from ..git import apply_selected_diff_lines
from .review_diff import (
    ADDED_FILTER,
    FULL_FILTER,
    REMOVED_FILTER,
    ReviewDiffView,
    comment_title,
    visible_diff_lines,
)

if TYPE_CHECKING:
    from ..app import FaltooChatApp
    from ..review import ReviewView

UNIFIED_LAYOUT = "unified"
SIDE_BY_SIDE_LAYOUT = "side-by-side"


class ReviewFileView(Static):
    DEFAULT_CSS = """
    ReviewFileView {
        width: 1fr;
        height: 1fr;
        border: round $panel;
    }

    ReviewFileView:focus-within {
        border: round $primary;
    }

    #review-file-layout {
        width: 1fr;
        height: 1fr;
    }

    #review-file-layout > ReviewDiffView {
        width: 1fr;
        height: 1fr;
    }

    ReviewFileView.side-by-side #review-file-right {
        border-left: wide $panel;
    }
    """

    def __init__(
        self,
        *,
        file_path: Path,
        review_view: "ReviewView",
        **viewer_kwargs: Any,
    ) -> None:
        super().__init__()
        self.file_path = file_path
        self.review_view = review_view
        self.diff: Diff = []
        self.loaded = False
        self.layout_mode = UNIFIED_LAYOUT
        self._side_by_side_rows_cache_key: tuple[int, int, int, int] | None = None
        self._side_by_side_rows_cache: (
            tuple[
                list[int | None], list[int | None], list[int | None], list[int | None]
            ]
            | None
        ) = None
        self._paired_diff_lines: dict[int, int] = {}
        self.left_viewer = ReviewDiffView(
            self.diff,
            file_path=self.file_path,
            review_view=self.review_view,
            filter_mode=FULL_FILTER,
            file_view=self,
            id="review-file-left",
            **viewer_kwargs,
        )
        self.right_viewer = ReviewDiffView(
            self.diff,
            file_path=self.file_path,
            review_view=self.review_view,
            filter_mode=ADDED_FILTER,
            file_view=self,
            id="review-file-right",
            **viewer_kwargs,
        )
        self._active_viewer: ReviewDiffView = self.left_viewer
        self._update_cursor_line_highlight()

    @property
    def active_viewer(self) -> ReviewDiffView:
        return self._active_viewer

    @active_viewer.setter
    def active_viewer(self, viewer: ReviewDiffView) -> None:
        previous = self._active_viewer
        self._active_viewer = viewer
        self._update_cursor_line_highlight()
        if previous is not viewer:
            previous.refresh()
            viewer.refresh()

    def compose(self) -> ComposeResult:
        yield Horizontal(self.left_viewer, self.right_viewer, id="review-file-layout")

    def on_mount(self) -> None:
        self._apply_layout_mode()

    def on_show(self) -> None:
        if self.review_view.active_file == self.file_path:
            self.call_after_refresh(self.focus_active_viewer)

    def update_border_labels(self) -> None:
        if not hasattr(self, "left_viewer"):
            return
        self.border_title = comment_title(self.left_viewer)
        self.border_subtitle = str(self.file_path)

    def _apply_layout_mode(self, *, force: bool = False) -> None:
        self.set_class(self.layout_mode == SIDE_BY_SIDE_LAYOUT, "side-by-side")
        if self.layout_mode == SIDE_BY_SIDE_LAYOUT:
            self.left_viewer.filter_mode = REMOVED_FILTER
            self.right_viewer.filter_mode = ADDED_FILTER
            self.right_viewer.can_focus = True
            self.right_viewer.display = True
            self._apply_side_by_side_rows(force=force)
            self.update_border_labels()
            return
        self.left_viewer.set_filter_mode(
            FULL_FILTER, force=force, preserve_cursor=False
        )
        self.right_viewer.display = False
        self.right_viewer.can_focus = False
        self.active_viewer = self.left_viewer
        self.update_border_labels()

    def _apply_side_by_side_rows(self, *, force: bool = False) -> None:
        # Derive pane widths from the parent container instead of waiting for
        # child TextAreas to report their post-layout widths.
        added_width, removed_width = self._side_by_side_text_widths()
        key = (id(self.diff), added_width, removed_width, self.left_viewer.indent_width)
        if (
            key == self._side_by_side_rows_cache_key
            and self._side_by_side_rows_cache is not None
        ):
            added_rows, removed_rows, added_map, removed_map = (
                self._side_by_side_rows_cache
            )
        else:
            # Projection is pure for a diff identity + widths, so cache it until
            # reload or resize changes the key.
            added_rows, removed_rows, added_map, removed_map = (
                _side_by_side_visible_diff_lines(
                    self.diff,
                    added_width=added_width,
                    removed_width=removed_width,
                    indent_width=self.left_viewer.indent_width,
                )
            )
            self._side_by_side_rows_cache_key = key
            self._side_by_side_rows_cache = (
                added_rows,
                removed_rows,
                added_map,
                removed_map,
            )

        self._paired_diff_lines = _side_by_side_paired_diff_lines(self.diff)
        self._set_viewer_rows(self.left_viewer, removed_rows, removed_map, force=force)
        self._set_viewer_rows(self.right_viewer, added_rows, added_map, force=force)

    def _side_by_side_text_widths(self) -> tuple[int, int]:
        cursor_width = 1
        if self.left_viewer.size.width and self.right_viewer.size.width:
            return (
                max(
                    0,
                    self.right_viewer.size.width
                    - self.right_viewer.gutter_width
                    - cursor_width,
                ),
                max(
                    0,
                    self.left_viewer.size.width
                    - self.left_viewer.gutter_width
                    - cursor_width,
                ),
            )

        content_width = self.content_size.width or max(0, self.size.width - 2)
        if content_width <= 0 and self.is_mounted:
            content_width = max(0, self.screen.size.width - 2)
        divider_width = 5
        available_width = max(0, content_width - divider_width)
        removed_pane_width = available_width - available_width // 2
        added_pane_width = available_width // 2
        gutter_width = max(
            self.left_viewer.gutter_width, self.right_viewer.gutter_width
        )
        return (
            max(0, added_pane_width - gutter_width - cursor_width),
            max(0, removed_pane_width - gutter_width - cursor_width),
        )

    def _set_viewer_rows(
        self,
        viewer: ReviewDiffView,
        rows: list[int | None],
        row_diff_lines: list[int | None] | None = None,
        force: bool = False,
    ) -> None:
        if not force and viewer.visible_diff_lines == rows:
            return
        diff_line = viewer.current_diff_line() if viewer.visible_diff_lines else None
        top_diff_line = (
            viewer.top_visible_diff_line() if viewer.visible_diff_lines else None
        )
        viewer.set_visible_diff_lines(rows, row_diff_lines)
        if diff_line is None or not viewer._visible_row_by_diff_line:
            return
        viewer.show_diff_line(diff_line, post_message=False)
        if top_diff_line is not None:
            viewer.scroll_top_to_diff_line(top_diff_line)

    def cycle_layout_mode(self, *, focus: bool = False) -> None:
        self.layout_mode = (
            SIDE_BY_SIDE_LAYOUT
            if self.layout_mode == UNIFIED_LAYOUT
            else UNIFIED_LAYOUT
        )
        self.apply_layout_mode(focus=focus)

    def apply_layout_mode(self, *, focus: bool = False) -> None:
        # Preserve the backing diff line across projection changes. Unloaded
        # file tabs have no projection yet, so they only need the layout state
        # applied; reload_in_place() will position them after diff loading.
        diff_line = (
            self.active_viewer.current_diff_line()
            if self.active_viewer.visible_diff_lines
            else None
        )
        self._apply_layout_mode()
        if diff_line is not None:
            self.show_diff_line(diff_line, center=True)
            self.set_timer(0.01, lambda: self.show_diff_line(diff_line, center=True))
        if focus:
            self.set_active_viewer(self.active_viewer, focus=True)

    def show_diff_line(
        self,
        diff_line: int,
        *,
        center: bool = False,
        focus: bool = False,
        preserve_active: bool = False,
    ) -> None:
        if self.layout_mode != SIDE_BY_SIDE_LAYOUT:
            self.set_active_viewer(self.left_viewer)
            self.left_viewer.show_diff_line(
                diff_line, center=center, post_message=False
            )
            return

        viewers = (self.left_viewer, self.right_viewer)
        real_viewers = [
            viewer
            for viewer in viewers
            if _viewer_has_real_diff_line(viewer, diff_line)
        ]
        if not preserve_active and self.active_viewer not in real_viewers:
            if real_viewers:
                self.set_active_viewer(real_viewers[0])
            elif diff_line not in self.active_viewer._visible_row_by_diff_line:
                self.set_active_viewer(
                    next(
                        (
                            viewer
                            for viewer in viewers
                            if diff_line in viewer._visible_row_by_diff_line
                        ),
                        self.left_viewer,
                    ),
                )
        if focus:
            self.set_active_viewer(self.active_viewer, focus=True)
        for viewer in viewers:
            if diff_line in viewer._visible_row_by_diff_line:
                viewer.show_diff_line(diff_line, center=center, post_message=False)
        sibling = self._sibling(self.active_viewer)
        if sibling is not None and diff_line not in sibling._visible_row_by_diff_line:
            self._sync_sibling_cursor_to_row(self.active_viewer.cursor_location[0])
        self._sync_sibling_scroll(self.active_viewer.scroll_offset[1])

    def set_active_viewer(self, viewer: ReviewDiffView, *, focus: bool = False) -> None:
        self.active_viewer = viewer
        self.review_view.active_pane = viewer
        if focus:
            self.focus_active_viewer()

    def _update_cursor_line_highlight(self) -> None:
        self.left_viewer.highlight_cursor_line = self.active_viewer is self.left_viewer
        self.right_viewer.highlight_cursor_line = (
            self.active_viewer is self.right_viewer
        )

    def focus_active_viewer(self) -> None:
        if not self.is_mounted:
            return
        try:
            self.screen.set_focus(self.active_viewer, scroll_visible=False)
        except NoScreen:
            return

    def focus_other_viewer(self) -> None:
        if self.layout_mode != SIDE_BY_SIDE_LAYOUT:
            return
        source = self.active_viewer
        scroll_y = source.scroll_offset[1]
        viewer = self.right_viewer if source == self.left_viewer else self.left_viewer
        self.set_active_viewer(viewer, focus=True)
        current_line = source.current_diff_line()
        target_line = self._paired_diff_lines.get(current_line)
        if not _show_focus_target(viewer, current_line, target_line):
            target_row = max(
                0, min(source.cursor_location[0], viewer.document.line_count - 1)
            )
            viewer.move_cursor(
                (
                    target_row,
                    min(
                        source.cursor_location[1],
                        len(viewer.document.get_line(target_row)),
                    ),
                ),
                record_width=False,
                post_message=False,
            )
        viewer.scroll_to(
            viewer.scroll_offset[0],
            scroll_y,
            animate=False,
            immediate=True,
        )

    def _sibling(self, viewer: ReviewDiffView) -> ReviewDiffView | None:
        if self.layout_mode != SIDE_BY_SIDE_LAYOUT:
            return None
        return self.right_viewer if viewer is self.left_viewer else self.left_viewer

    def needs_reload(self) -> bool:
        return not self.loaded

    async def reload_in_place(self) -> None:
        workspace = cast("FaltooChatApp", self.app).workspace
        active = self.active_viewer

        # Remember view state in backing-diff coordinates before replacing text.
        diff_line = active.current_diff_line() if active.visible_diff_lines else 0
        top_diff_line = (
            active.top_visible_diff_line() if active.visible_diff_lines else 0
        )
        selection = active.selection

        # Load the canonical diff once, reset cached projections, and push the
        # same diff down to both child viewers.
        self.diff = await asyncio.to_thread(get_diff, workspace / self.file_path)
        self._side_by_side_rows_cache_key = None
        self._side_by_side_rows_cache = None
        self.loaded = True
        for viewer in (self.left_viewer, self.right_viewer):
            viewer.diff = self.diff

        # Reapply projection and restore cursor/scroll for the current layout.
        self._apply_layout_mode(force=True)
        viewers = (
            (self.left_viewer, self.right_viewer)
            if self.layout_mode == SIDE_BY_SIDE_LAYOUT
            else (self.left_viewer,)
        )
        for viewer in viewers:
            if viewer._visible_row_by_diff_line:
                viewer.show_diff_line(diff_line, post_message=False)
                viewer.scroll_top_to_diff_line(top_diff_line)
        self._sync_sibling_cursor_to_row(self.active_viewer.cursor_location[0])
        if active.document.line_count:
            max_line = active.document.line_count - 1

            def clamp(location: tuple[int, int]) -> tuple[int, int]:
                line = max(0, min(location[0], max_line))
                column = max(0, min(location[1], len(active.document.get_line(line))))
                return (line, column)

            active.line_selection_anchor = None
            active.line_selection_cursor = None
            active.selection = type(active.selection)(
                clamp(selection.start),
                clamp(selection.end),
            )
        if self.review_view.active_file == self.file_path:
            self.focus_active_viewer()

    def jump_to_file_line(self, line_number: int) -> None:
        for viewer in (self.left_viewer, self.right_viewer):
            viewer.jump_to_file_line(line_number)
        self.focus_active_viewer()

    def on_review_diff_view_focused_pane(
        self, event: ReviewDiffView.FocusedPane
    ) -> None:
        self.set_active_viewer(event.viewer)

    def on_review_diff_view_cycle_layout_requested(
        self, event: ReviewDiffView.CycleLayoutRequested
    ) -> None:
        event.stop()
        self.cycle_layout_mode(focus=True)

    def on_review_diff_view_focus_other_pane_requested(
        self, _event: ReviewDiffView.FocusOtherPaneRequested
    ) -> None:
        self.focus_other_viewer()

    def on_review_diff_view_modification_jump_requested(
        self, event: ReviewDiffView.ModificationJumpRequested
    ) -> None:
        event.stop()
        current_line = self.active_viewer.current_diff_line()
        target_line = (
            next_modification(self.diff, current_line)
            if event.delta > 0
            else previous_modification(self.diff, current_line)
        )
        if target_line is not None:
            if (
                self.layout_mode == SIDE_BY_SIDE_LAYOUT
                and target_line not in self.active_viewer._visible_row_by_diff_line
            ):
                target_line = self.active_viewer._visible_diff_line(
                    self.active_viewer._display_line(target_line)
                )
            self.show_diff_line(
                target_line, center=True, focus=True, preserve_active=True
            )

    async def on_review_diff_view_refresh_requested(
        self, event: ReviewDiffView.RefreshRequested
    ) -> None:
        event.stop()
        await self.reload_in_place()

    def on_review_diff_view_cursor_moved(
        self, event: ReviewDiffView.CursorMoved
    ) -> None:
        self.update_cursor_from_viewer(event.viewer, center=event.center)

    def on_review_diff_view_scrolled(self, event: ReviewDiffView.Scrolled) -> None:
        self.update_scroll_from_viewer(event.viewer)

    def update_cursor_from_viewer(
        self, viewer: ReviewDiffView, *, center: bool = False
    ) -> None:
        if viewer is not self.active_viewer:
            return
        self._sync_sibling_cursor_to_display_row(
            _cursor_display_row(viewer), center=center
        )

    def _sync_sibling_cursor_to_display_row(
        self, display_row: int, *, center: bool = False
    ) -> None:
        sibling = self._sibling(self.active_viewer)
        if sibling is None or sibling.document.line_count == 0:
            return
        sibling.move_cursor(
            _cursor_location_for_display_row(sibling, display_row),
            center=center,
            record_width=False,
            post_message=False,
        )

    def _sync_sibling_cursor_to_row(self, row: int, *, center: bool = False) -> None:
        sibling = self._sibling(self.active_viewer)
        if sibling is None or sibling.document.line_count == 0:
            return
        target_row = max(0, min(row, sibling.document.line_count - 1))
        column = min(
            self.active_viewer.cursor_location[1],
            len(sibling.document.get_line(target_row)),
        )
        sibling.move_cursor(
            (target_row, column),
            center=center,
            record_width=False,
            post_message=False,
        )

    def update_scroll_from_viewer(self, viewer: ReviewDiffView) -> None:
        if viewer is not self.active_viewer:
            return
        self._sync_sibling_scroll(viewer.scroll_offset[1])

    def _sync_sibling_scroll(self, scroll_y: float) -> None:
        sibling = self._sibling(self.active_viewer)
        if sibling is None:
            return
        sibling.scroll_to(
            sibling.scroll_offset[0],
            scroll_y,
            animate=False,
            immediate=True,
        )

    def on_resize(self, _event: events.Resize) -> None:
        if self.layout_mode == SIDE_BY_SIDE_LAYOUT and self.loaded:
            self._apply_side_by_side_rows()

    async def stage_visible_rows(
        self,
        viewer: ReviewDiffView,
        *,
        visible_rows: set[int],
    ) -> None:
        # Staging is handled here because this widget owns the canonical diff
        # shared by both side-by-side panes.
        selected_diff_lines = {
            diff_line
            for row in visible_rows
            if 0 <= row < len(viewer.visible_diff_lines)
            if (diff_line := viewer.visible_diff_lines[row]) is not None
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
        viewer.selection = type(viewer.selection).cursor(viewer.cursor_location)
        viewer.line_selection_anchor = None
        viewer.line_selection_cursor = None
        await self.reload_in_place()


def _cursor_display_row(viewer: ReviewDiffView) -> int:
    return int(
        getattr(
            viewer,
            "_cursor_offset",
            (0, viewer._first_display_row(viewer.cursor_location[0])),
        )[1]
    )


def _cursor_location_for_display_row(
    viewer: ReviewDiffView, display_row: int
) -> tuple[int, int]:
    display_row = max(0, min(display_row, viewer.wrapped_document.height - 1))
    line_info = viewer._display_line_info(display_row)
    if line_info is None:
        line = min(display_row, viewer.document.line_count - 1)
        return line, 0
    line, column = line_info
    line = max(0, min(line, viewer.document.line_count - 1))
    return line, min(column, len(viewer.document.get_line(line)))


def _wrapped_height(text: str, width: int, *, indent_width: int) -> int:
    if width <= 0:
        return 1
    return len(compute_wrap_offsets(text, width, tab_size=indent_width)) + 1


def _side_by_side_visible_diff_lines(
    diff: Diff,
    *,
    added_width: int,
    removed_width: int,
    indent_width: int,
) -> tuple[list[int | None], list[int | None], list[int | None], list[int | None]]:
    added_rows = visible_diff_lines(diff, ADDED_FILTER)
    removed_rows = visible_diff_lines(diff, REMOVED_FILTER)
    padded_added: list[int | None] = []
    padded_removed: list[int | None] = []
    added_map: list[int | None] = []
    removed_map: list[int | None] = []
    for added, removed in zip(added_rows, removed_rows, strict=True):
        added_anchor = added if added is not None else removed
        removed_anchor = removed if removed is not None else added
        padded_added.append(added)
        padded_removed.append(removed)
        added_map.append(added_anchor)
        removed_map.append(removed_anchor)
        added_type = "" if added is None else diff[added]["type"]
        removed_type = "" if removed is None else diff[removed]["type"]
        if (
            added_width <= 0
            or removed_width <= 0
            or not (added is None or removed is None or added_type or removed_type)
        ):
            continue
        added_text = "" if added is None else diff[added]["text"]
        removed_text = "" if removed is None else diff[removed]["text"]
        added_height = _wrapped_height(
            added_text, added_width, indent_width=indent_width
        )
        removed_height = _wrapped_height(
            removed_text, removed_width, indent_width=indent_width
        )
        if added_height > removed_height:
            extra_rows = added_height - removed_height
            padded_removed.extend([None] * extra_rows)
            removed_map.extend([added_anchor] * extra_rows)
        elif removed_height > added_height:
            extra_rows = removed_height - added_height
            padded_added.extend([None] * extra_rows)
            added_map.extend([removed_anchor] * extra_rows)
    return padded_added, padded_removed, added_map, removed_map


def _side_by_side_paired_diff_lines(diff: Diff) -> dict[int, int]:
    pairs: dict[int, int] = {}
    for added, removed in zip(
        visible_diff_lines(diff, ADDED_FILTER),
        visible_diff_lines(diff, REMOVED_FILTER),
        strict=True,
    ):
        if added is not None and removed is not None and added != removed:
            pairs[added] = removed
            pairs[removed] = added
    return pairs


def _show_focus_target(
    viewer: ReviewDiffView, current_line: int, target_line: int | None
) -> bool:
    candidates = [current_line] + ([] if target_line is None else [target_line])
    for diff_line in candidates:
        if _viewer_has_real_diff_line(viewer, diff_line):
            viewer.show_diff_line(diff_line, post_message=False)
            return True
    for diff_line in candidates:
        if diff_line in viewer._visible_row_by_diff_line:
            viewer.show_diff_line(diff_line, post_message=False)
            return True
    return False


def _viewer_has_real_diff_line(viewer: ReviewDiffView, diff_line: int) -> bool:
    row = viewer._visible_row_by_diff_line.get(diff_line)
    return row is not None and viewer.visible_diff_lines[row] == diff_line
