import asyncio
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias, cast

from textual.app import ComposeResult
from textual.css.query import NoMatches
from textual.binding import Binding
from textual.widgets import Static, TabbedContent, TabPane, Tabs

from .git import get_unstaged_files, is_git_workspace
from .review_api import Review, Reviews, review_to_message_item, upsert_review
from .widgets import ReviewDiffView, SearchProject

if TYPE_CHECKING:
    from .app import FaltooChatApp
    from .widgets.search_project import ProjectSearchResult

ModifiedFiles: TypeAlias = list[Path]


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


NO_CHANGES_PANE_ID = "no-changes"


def _syntax_highlight_theme(app_theme: str) -> str:
    return "github_light" if "light" in app_theme else "vscode_dark"


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
    BINDINGS = [
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
        border: round $panel;
    }

    ReviewDiffView:focus {
        border: round $primary;
    }
    """

    def __init__(self) -> None:
        super().__init__("Review [Ctrl+R]", id="review-tab")
        self.active_pane: ReviewDiffView | None = None
        self.reviews: Reviews = []
        self.extra_paths: list[Path] = []
        self.search_term = ""
        self.search_whole_word = False
        self.soft_wrap_enabled = True
        self.line_highlights = True

    def compose(self) -> ComposeResult:
        with TabbedContent(initial=NO_CHANGES_PANE_ID, id="review-tabs"):
            with TabPane("Review", id=NO_CHANGES_PANE_ID):
                yield ReviewEmpty("No modified files yet.", id="review-empty")

    async def _add_file_pane(self, path: Path, title: str) -> None:
        await self.query_one("#review-tabs", TabbedContent).add_pane(
            TabPane(
                title,
                ReviewDiffView(
                    [],
                    file_path=path,
                    review_view=self,
                    language=LANGUAGES_BY_SUFFIX.get(path.suffix.lower()),
                    theme=_syntax_highlight_theme(self.app.theme),
                    soft_wrap=self.soft_wrap_enabled,
                    line_highlights=self.line_highlights,
                    read_only=True,
                    show_cursor=True,
                    show_line_numbers=True,
                    highlight_cursor_line=True,
                ),
                id=_get_tab_id(path),
            )
        )

    def set_display_preferences(
        self,
        *,
        soft_wrap: bool | None = None,
        line_highlights: bool | None = None,
    ) -> None:
        """Apply shared display preferences to all open review diff viewers."""
        if soft_wrap is not None:
            self.soft_wrap_enabled = soft_wrap
        if line_highlights is not None:
            self.line_highlights = line_highlights
        for viewer in self.query(ReviewDiffView):
            if soft_wrap is not None:
                viewer.soft_wrap = soft_wrap
            if line_highlights is not None:
                viewer.line_highlights = line_highlights
                viewer.refresh()

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
        app.run_worker(app.submit_message(message_item), exclusive=True)
        self.reviews.clear()

    def action_review_refresh_files(self) -> None:
        self.app.run_worker(
            self.refresh_files(close_unmodified=True),
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
                    line_number=result["line_number"],
                )
            )

        app.push_screen(SearchProject(workspace=workspace), on_result)

    def set_active_tab(self, path: Path) -> bool:
        tabs = self.query_one("#review-tabs", TabbedContent)
        pane = _get_file_pane(tabs, path)
        if pane is None or not pane.query(ReviewDiffView):
            self.active_pane = None
            return False
        self.active_pane = pane.query_one(ReviewDiffView)
        tabs.active = _get_tab_id(path)
        self.active_pane.focus()
        return True

    async def close_stale_file(self, path: Path) -> bool:
        workspace = cast("FaltooChatApp", self.app).workspace
        if (workspace / path).exists():
            return False
        _message, files = await asyncio.to_thread(
            _get_modified_files,
            workspace,
            self.extra_paths,
        )
        if path in files:
            return False
        self.extra_paths = [item for item in self.extra_paths if item != path]
        tabs = self.query_one("#review-tabs", TabbedContent)
        pane = _get_file_pane(tabs, path)
        if pane is None or pane.id is None:
            return False
        await tabs.remove_pane(pane.id)
        if self.active_pane is not None and self.active_pane.file_path == path:
            self.active_pane = None
        return True

    async def open_file(
        self,
        path: Path,
        *,
        line_number: int | None = None,
    ) -> None:
        tabs = self.query_one("#review-tabs", TabbedContent)
        pane = _get_file_pane(tabs, path)
        if pane is None:
            if path not in self.extra_paths:
                self.extra_paths.append(path)
            await self.refresh_files()
        if not self.set_active_tab(path) or self.active_pane is None:
            return
        if line_number is None:
            return
        await self.active_pane.reload_in_place()
        self.active_pane.jump_to_file_line(line_number)

    async def _add_missing_file_tabs(self, files: ModifiedFiles) -> None:
        """Open review tabs for modified files that do not already have a tab."""
        try:
            tabs = self.query_one("#review-tabs", TabbedContent)
        except NoMatches:
            return
        titles = _review_tab_titles(files)
        for path in files:
            if _get_file_pane(tabs, path) is not None:
                continue
            await self._add_file_pane(path, titles[path])

    async def _replace_file_tabs(self, files: ModifiedFiles) -> None:
        """Make review file tabs match the given modified file list."""
        try:
            tabs = self.query_one("#review-tabs", TabbedContent)
        except NoMatches:
            return
        keep_paths = set(files)
        for pane in list(tabs.query(TabPane)):
            if pane.id is None or pane.id == NO_CHANGES_PANE_ID:
                continue
            viewer = pane.query_one(ReviewDiffView)
            if viewer.file_path in keep_paths:
                continue
            await tabs.remove_pane(pane.id)
        await self._add_missing_file_tabs(files)

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
        self.active_pane = None
        # comment: startup refresh can run before the empty placeholder finishes mounting.
        if self.query("#review-empty"):
            self.query_one("#review-empty", ReviewEmpty).focus()

    async def refresh_files(self, *, close_unmodified: bool = False) -> None:
        """Refresh review tabs from git, optionally closing tabs without modifications."""
        # comment: review refresh can be queued before the nested review tabs finish mounting.
        try:
            tabs = self.query_one("#review-tabs", TabbedContent)
        except NoMatches:
            return

        active_path = None if self.active_pane is None else self.active_pane.file_path
        workspace = cast("FaltooChatApp", self.app).workspace

        if close_unmodified:
            message, files = await asyncio.to_thread(_get_modified_files, workspace)
            self.extra_paths = [path for path in self.extra_paths if path in files]
        else:
            # comment: manually opened files may be deleted outside the app, so drop missing paths.
            self.extra_paths = [
                path for path in self.extra_paths if (workspace / path).is_file()
            ]
            message, files = await asyncio.to_thread(
                _get_modified_files,
                workspace,
                self.extra_paths,
            )

        # comment: when there are no reviewable files, clear file tabs and return to the empty tab.
        if message is not None:
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

        if close_unmodified:
            await self._replace_file_tabs(files)
        else:
            await self._add_missing_file_tabs(files)

        # comment: keep the current file active when it still exists after the refresh.
        if active_path is not None and self.set_active_tab(active_path):
            return

        # comment: if nothing was active yet, focus the first available review tab.
        viewers = list(self.query(ReviewDiffView))
        if not viewers:
            self.active_pane = None
            return
        self.set_active_tab(viewers[0].file_path)
