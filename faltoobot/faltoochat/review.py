import asyncio
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widgets import Static, TabbedContent, TabPane, Tabs


from .git import get_unstaged_files, is_git_workspace
from .review_api import Review, Reviews, review_to_message_item, upsert_review
from .widgets import ReviewDiffView, ReviewFileView, SearchProject

if TYPE_CHECKING:
    from .app import FaltooChatApp
    from .widgets.search_project import ProjectSearchResult

ModifiedFiles: TypeAlias = list[Path]
ReviewFilesSignature: TypeAlias = tuple[tuple[str, int, int], ...]


NO_CHANGES_PANE_ID = "no-changes"


def _get_tab_id(path: Path) -> str:
    # comment: file paths can contain characters that are awkward in Textual ids, so hash them
    # into a short stable tab id that still maps one-to-one with the file path.
    digest = hashlib.md5(str(path).encode("utf-8")).hexdigest()[:8]
    return f"review-file-{digest}"


def _get_modified_files(
    workspace: Path,
    extra_paths: list[Path] | None = None,
) -> tuple[str | None, ModifiedFiles]:
    if not is_git_workspace(workspace):
        return "Git repository not found.", []

    files = list(get_unstaged_files(workspace))
    for path in extra_paths or []:
        full_path = workspace / path
        # comment: a manually opened file may disappear between refreshes, so skip missing paths.
        if not full_path.is_file() or path in files:
            continue
        files.append(path)

    if not files:
        return "No modified files yet.", []
    return None, files


def _review_files_signature(
    workspace: Path, files: ModifiedFiles
) -> ReviewFilesSignature:
    signature: list[tuple[str, int, int]] = []
    index = workspace / ".git" / "index"
    if index.exists():
        stat = index.stat()
        signature.append((".git/index", stat.st_mtime_ns, stat.st_size))
    for path in files:
        full_path = workspace / path
        if not full_path.exists():
            continue
        stat = full_path.stat()
        signature.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(signature)


def _review_tab_titles(files: ModifiedFiles) -> dict[Path, str]:
    counts: dict[str, int] = {}
    for path in files:
        counts[path.name] = counts.get(path.name, 0) + 1
    return {
        path: (path.name if counts[path.name] == 1 else str(path)) for path in files
    }


def _get_file_pane(tabs: TabbedContent, path: Path) -> TabPane | None:
    pane_id = _get_tab_id(path)
    return next((pane for pane in tabs.query(TabPane) if pane.id == pane_id), None)


class ReviewEmpty(Static):
    can_focus = True


class ReviewView(TabPane):
    review_files = reactive[tuple[Path, ...]](())
    active_file = reactive[Path | None](None)
    line_highlights = reactive(True)

    DEFAULT_BINDINGS = [
        Binding(
            "@", "review_search_project", "Search Project", priority=True, show=True
        ),
        Binding("R", "review_refresh_files", "Refresh Files", priority=True, show=True),
    ]

    DEFAULT_CSS = """
    ReviewView {
        width: 1fr;
        height: 1fr;
    }

    #review-tabs {
        width: 1fr;
        height: 1fr;
    }

    #review-empty {
        width: 1fr;
        height: 1fr;
        color: $text-muted;
        content-align: center middle;
    }

    ReviewDiffView {
        width: 1fr;
        height: 1fr;
    }
    """

    def __init__(self) -> None:
        super().__init__("Review [Ctrl+R]", id="review-tab")
        self.active_pane: ReviewDiffView | None = None
        self.reviews: Reviews = []
        self.search_term = ""
        self.search_whole_word = False
        self._review_files_signature: ReviewFilesSignature | None = None

    def compose(self) -> ComposeResult:
        with TabbedContent(initial=NO_CHANGES_PANE_ID, id="review-tabs"):
            with TabPane("Review", id=NO_CHANGES_PANE_ID):
                yield ReviewEmpty("No modified files yet.", id="review-empty")

    def on_show(self) -> None:
        if self.active_pane is not None:
            self.call_after_refresh(self.active_pane.file_view.focus_active_viewer)

    async def _add_file_pane(self, path: Path, title: str) -> None:
        await self.query_one("#review-tabs", TabbedContent).add_pane(
            TabPane(
                title,
                ReviewFileView(
                    file_path=path,
                    review_view=self,
                    soft_wrap=True,
                    line_highlights=self.line_highlights,
                    read_only=True,
                    show_cursor=True,
                    show_line_numbers=True,
                    highlight_cursor_line=True,
                ),
                id=_get_tab_id(path),
            )
        )

    def set_display_preferences(self, *, line_highlights: bool | None = None) -> None:
        """Apply shared display preferences to all open review diff viewers."""
        if line_highlights is not None:
            self.line_highlights = line_highlights

    def watch_line_highlights(self, line_highlights: bool) -> None:
        for viewer in self.query(ReviewDiffView):
            viewer.line_highlights = line_highlights
            viewer.refresh()

    async def on_review_diff_view_file_tab_cycle_requested(
        self, event: ReviewDiffView.FileTabCycleRequested
    ) -> None:
        event.stop()
        await self.cycle_active_file(event.delta)

    def on_review_diff_view_open_split_requested(
        self, event: ReviewDiffView.OpenSplitRequested
    ) -> None:
        event.stop()
        app = cast("FaltooChatApp", self.app)
        file_view = event.viewer.file_view

        async def open_result(result: "ProjectSearchResult") -> None:
            await file_view.open_split(result["path"])
            if result["line_number"] is not None:
                file_view.active_viewer.jump_to_file_line(result["line_number"])
            self.active_pane = file_view.active_viewer

        def on_result(result: "ProjectSearchResult | None") -> None:
            if result is not None:
                asyncio.create_task(open_result(result))

        app.push_screen(
            SearchProject(
                workspace=app.workspace,
                preferred_files=list(self.review_files),
            ),
            on_result,
        )

    def add_review(self, review: Review) -> None:
        result = upsert_review(self.reviews, review)
        if result == "ignored":
            return
        messages = {
            "added": "Review added.",
            "updated": "Review updated.",
            "deleted": "Review removed.",
        }
        self.app.notify(messages[result])

    async def submit_reviews(self) -> None:
        if not self.reviews:
            self.app.notify("No reviews added yet.", severity="warning")
            return
        app = cast("FaltooChatApp", self.app)
        app.action_show_chat_tab()
        message_item = review_to_message_item(self.reviews)
        if app.is_answering:
            await app.queue().add_to_queue(message_item)
            app.focus_composer()
            self.reviews.clear()
            return
        await app.handle_message(message_item)
        self.reviews.clear()

    def action_review_refresh_files(self) -> None:
        self.app.run_worker(
            self.refresh_files(),
            group="review-refresh",
            exclusive=True,
        )

    def action_review_search_project(self) -> None:
        app = cast("FaltooChatApp", self.app)
        workspace = app.workspace

        def on_result(result: "ProjectSearchResult | None") -> None:
            if result is None:
                return
            asyncio.create_task(
                self.open_file(
                    result["path"],
                    line_number=result["line_number"] or 1,
                )
            )

        app.push_screen(
            SearchProject(
                workspace=workspace,
                preferred_files=list(self.review_files),
            ),
            on_result,
        )

    def watch_active_file(self, path: Path | None) -> None:
        if path is None:
            self.active_pane = None
            return
        tabs = self.query_one("#review-tabs", TabbedContent)
        pane = _get_file_pane(tabs, path)
        if pane is None:
            return
        if tabs.active != pane.id:
            tabs.active = pane.id or ""
        file_view = pane.query_one(ReviewFileView)
        file_view.focus_active_viewer()
        self.active_pane = file_view.active_viewer
        if not file_view.viewer.loaded:
            self.app.run_worker(
                file_view.reload_in_place(),
                group=f"review-load-{path}",
                exclusive=True,
            )

    def on_tabbed_content_tab_activated(
        self,
        event: TabbedContent.TabActivated,
    ) -> None:
        if event.pane.id == NO_CHANGES_PANE_ID:
            self.active_file = None
            return
        self.active_file = event.pane.query_one(ReviewFileView).file_path

    async def cycle_active_file(self, delta: int) -> None:
        files = list(self.review_files)
        index = files.index(self.active_file) if self.active_file in files else 0
        self.active_file = files[(index + delta) % len(files)]

    async def open_file(
        self,
        path: Path,
        *,
        line_number: int = 1,
    ) -> None:
        tabs = self.query_one("#review-tabs", TabbedContent)
        if path not in self.review_files:
            self.review_files = (*self.review_files, path)
            await self._add_file_pane(
                path, _review_tab_titles(list(self.review_files))[path]
            )
        self.active_file = path
        pane = cast(TabPane, _get_file_pane(tabs, path))
        file_view = pane.query_one(ReviewFileView)
        await file_view.reload_in_place()
        file_view.active_viewer = file_view.viewer
        file_view.viewer.jump_to_file_line(line_number)
        file_view.focus_active_viewer()
        self.active_pane = file_view.viewer

    async def _replace_file_tabs(
        self, tabs: TabbedContent, files: ModifiedFiles
    ) -> None:
        self.review_files = tuple(files)
        keep_paths = set(self.review_files)
        for pane in list(tabs.query(TabPane)):
            if pane.id == NO_CHANGES_PANE_ID:
                continue
            if pane.query_one(ReviewFileView).file_path not in keep_paths:
                await tabs.remove_pane(pane.id or "")
        titles = _review_tab_titles(files)
        for path in self.review_files:
            if _get_file_pane(tabs, path) is None:
                await self._add_file_pane(path, titles[path])

    async def clear_all_tabs(self) -> None:
        """Remove all file review tabs and show the default empty review tab."""
        try:
            tabs = self.query_one("#review-tabs", TabbedContent)
        except NoMatches:
            return
        # comment: TabbedContent builds its internal tab strip asynchronously during startup.
        if not tabs.query(Tabs):
            return
        for pane in tabs.query(TabPane):
            if pane.id is None or pane.id == NO_CHANGES_PANE_ID:
                continue
            await tabs.remove_pane(pane.id)
        # comment: early refresh can run before the nested tab widget fully builds the tab entry
        # for the empty pane, so showing it can briefly fail during startup.
        try:
            tabs.show_tab(NO_CHANGES_PANE_ID)
        except Tabs.TabError:
            pass
        tabs.active = NO_CHANGES_PANE_ID
        self.review_files = ()
        self.active_file = None
        self.active_pane = None
        # comment: startup refresh can run before the empty placeholder finishes mounting.
        if self.query("#review-empty"):
            self.query_one("#review-empty", ReviewEmpty).focus()

    async def refresh_files(self) -> None:
        """Refresh review tabs from git and close files that are no longer modified."""
        tabs = self.query_one("#review-tabs", TabbedContent)

        active_path = self.active_file
        old_files = list(self.review_files)
        active_index = (
            old_files.index(active_path)
            if active_path is not None and active_path in old_files
            else 0
        )
        workspace = cast("FaltooChatApp", self.app).workspace

        message, files = await asyncio.to_thread(_get_modified_files, workspace)
        signature = _review_files_signature(workspace, files)

        active_needs_reload = (
            self.active_pane is not None and not self.active_pane.loaded
        )
        if (
            signature == self._review_files_signature
            and tuple(files) == self.review_files
            and not active_needs_reload
        ):
            return

        # comment: when there are no reviewable files, clear file tabs and return to the empty tab.
        if message is not None:
            self._review_files_signature = signature
            await self.clear_all_tabs()
            self.app.notify(message)
            return

        # comment: TabbedContent builds its internal tab strip asynchronously during startup.
        if not tabs.query(Tabs):
            return
        try:
            tabs.hide_tab(NO_CHANGES_PANE_ID)
        except (NoMatches, Tabs.TabError):
            return

        await self._replace_file_tabs(tabs, files)
        self._review_files_signature = signature

        if active_path is not None and active_path in self.review_files:
            self.active_file = active_path
            if self.active_pane is not None:
                await self.active_pane.file_view.reload_in_place()
            return

        # comment: if nothing was active yet, focus the first available review tab.
        if not self.review_files:
            self.active_file = None
            self.active_pane = None
            return
        self.active_file = self.review_files[
            min(active_index, len(self.review_files) - 1)
        ]
