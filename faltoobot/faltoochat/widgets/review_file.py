from pathlib import Path
from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.dom import NoScreen
from textual.widgets import Static

from .review_diff import ReviewDiffView

if TYPE_CHECKING:
    from ..review import ReviewView


class ReviewFileView(Static):
    DEFAULT_CSS = """
    ReviewFileView {
        width: 1fr;
        height: 1fr;
    }

    #review-file-split {
        width: 1fr;
        height: 1fr;
    }

    ReviewDiffView {
        width: 1fr;
        height: 1fr;
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
        self.viewer = ReviewDiffView(
            [],
            file_path=self.file_path,
            review_view=self.review_view,
            file_view=self,
            id="review-file-viewer",
            **viewer_kwargs,
        )
        self.active_viewer = self.viewer
        self.right_viewer = ReviewDiffView(
            [],
            file_path=self.file_path,
            review_view=self.review_view,
            file_view=self,
            id="review-file-right",
            **viewer_kwargs,
        )
        self.right_viewer.display = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="review-file-split"):
            yield self.viewer
            yield self.right_viewer

    def on_show(self) -> None:
        if self.review_view.active_file == self.file_path:
            self.call_after_refresh(self.focus_active_viewer)

    async def open_split(self, file_path: Path | None = None) -> None:
        diff_line = (
            self.viewer.current_diff_line() if self.viewer.visible_diff_lines else None
        )
        self.right_viewer.display = True
        self.right_viewer.file_path = file_path or self.file_path
        self.right_viewer.filter_mode = self.viewer.filter_mode
        if self.right_viewer.file_path == self.file_path:
            self.right_viewer.set_diff(list(self.viewer.diff))
        else:
            await self.right_viewer.reload_in_place()
        self.active_viewer = self.right_viewer
        if diff_line is not None:
            if self.is_mounted:
                self.call_after_refresh(
                    lambda: self.viewer.show_diff_line(diff_line, center=True)
                )
            else:
                self.viewer.show_diff_line(diff_line, center=True)
        self.focus_active_viewer()

    def close_split(self) -> None:
        diff_line = (
            self.viewer.current_diff_line() if self.viewer.visible_diff_lines else None
        )
        self.right_viewer.display = False
        self.active_viewer = self.viewer
        if diff_line is not None:
            if self.is_mounted:
                self.call_after_refresh(
                    lambda: self.viewer.show_diff_line(diff_line, center=True)
                )
            else:
                self.viewer.show_diff_line(diff_line, center=True)
        self.focus_active_viewer()

    def focus_other_viewer(self) -> None:
        if not self.right_viewer.display:
            return
        self.active_viewer = (
            self.right_viewer if self.active_viewer is self.viewer else self.viewer
        )
        self.focus_active_viewer()

    def focus_active_viewer(self) -> None:
        if not self.is_mounted:
            return
        try:
            self.screen.set_focus(self.active_viewer, scroll_visible=False)
        except NoScreen:
            return

    async def reload_in_place(self) -> None:
        await self.viewer.reload_in_place()
        if self.review_view.active_file == self.file_path:
            self.focus_active_viewer()

    def on_review_diff_view_focused_pane(
        self, event: ReviewDiffView.FocusedPane
    ) -> None:
        self.active_viewer = event.viewer
        self.review_view.active_pane = event.viewer

    def on_review_diff_view_focus_other_pane_requested(
        self, event: ReviewDiffView.FocusOtherPaneRequested
    ) -> None:
        event.stop()
        self.focus_other_viewer()

    def on_review_diff_view_close_split_requested(
        self, event: ReviewDiffView.CloseSplitRequested
    ) -> None:
        event.stop()
        self.close_split()

    async def on_review_diff_view_refresh_requested(
        self, event: ReviewDiffView.RefreshRequested
    ) -> None:
        event.stop()
        await event.viewer.reload_in_place()
