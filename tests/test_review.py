import asyncio
import subprocess
from pathlib import Path
import pytest

from faltoobot import sessions
from faltoobot.faltoochat.diff import get_diff
from faltoobot.faltoochat.app import FaltooChatApp
from faltoobot.faltoochat.review import (
    ReviewView,
    _review_tab_titles,
    _syntax_highlight_theme,
)
from faltoobot.faltoochat.widgets import (
    ReviewCommentModal,
    ReviewDiffView,
    SearchProject,
    Telescope,
)
from textual.widgets import Input, OptionList, TabPane, TabbedContent, TextArea
from textual.widgets.option_list import Option

EXPECTED_REVIEW_FILES = 2


def review_file_panes(tabs: TabbedContent) -> list[TabPane]:
    return [pane for pane in tabs.query(TabPane) if pane.id != "no-changes"]


def test_syntax_highlight_theme_matches_app_theme() -> None:
    assert _syntax_highlight_theme("textual-dark") == "vscode_dark"
    assert _syntax_highlight_theme("textual-light") == "github_light"


async def wait_for_condition(check) -> None:
    while True:
        if check():
            return
        await asyncio.sleep(0)


def git(workspace: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def build_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, FaltooChatApp]:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    git(workspace, "init")
    git(workspace, "config", "user.email", "tests@example.com")
    git(workspace, "config", "user.name", "Tests")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(workspace)
    return workspace, FaltooChatApp(
        session=sessions.get_session(
            chat_key=sessions.get_dir_chat_key(workspace),
            workspace=workspace,
        )
    )


def create_modified_files(workspace: Path) -> None:
    alpha = workspace / "alpha.py"
    beta = workspace / "beta.py"
    gamma = workspace / "gamma.py"
    alpha.write_text(
        "\n".join(
            [
                "a = 1",
                "b = 2",
                "c = 3",
                "d = 4",
                "e = 5",
                "f = 6",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    beta.write_text('value = "beta"\n', encoding="utf-8")
    gamma.write_text('value = "gamma"\n', encoding="utf-8")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "initial")

    alpha.write_text(
        "\n".join(
            [
                "a = 1",
                "b = 20",
                "c = 3",
                "d = 4",
                "e = 50",
                "f = 6",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    beta.write_text('value = "beta staged"\n', encoding="utf-8")
    git(workspace, "add", "beta.py")
    beta.write_text('value = "beta staged"\nextra = 1\n', encoding="utf-8")


async def open_review(app: FaltooChatApp, pilot) -> TabbedContent:
    await pilot.pause(0)
    await pilot.press("ctrl+2")
    await wait_for_condition(
        lambda: bool(app.query("#review-tabs")) and len(app.query("#review-tabs")) == 1
    )
    await pilot.pause(0)
    return app.query_one("#review-tabs", TabbedContent)


@pytest.mark.anyio
async def test_review_hides_staged_only_files_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    alpha = workspace / "alpha.py"
    beta = workspace / "beta.py"
    alpha.write_text("a = 1\n", encoding="utf-8")
    beta.write_text("b = 1\n", encoding="utf-8")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "initial")

    alpha.write_text("a = 2\n", encoding="utf-8")
    beta.write_text("b = 2\n", encoding="utf-8")
    git(workspace, "add", "beta.py")

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        assert {pane._title for pane in review_file_panes(review_tabs)} == {"alpha.py"}


@pytest.mark.anyio
async def test_review_tab_shows_modified_files_as_nested_tabs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        panes = review_file_panes(review_tabs)
        assert {pane._title for pane in panes} == {"alpha.py", "beta.py"}

        viewers = [viewer for viewer in review_tabs.query(ReviewDiffView)]
        assert len(viewers) == EXPECTED_REVIEW_FILES
        assert all(viewer.language == "python" for viewer in viewers)
        assert all("diff --git" not in viewer.text for viewer in viewers)
        assert any("b = 20" in viewer.text for viewer in viewers)

        beta_pane = next(
            pane for pane in review_tabs.query(TabPane) if pane._title == "beta.py"
        )
        review_tabs.active = beta_pane.id or ""
        await wait_for_condition(
            lambda: "beta staged" in beta_pane.query_one(ReviewDiffView).text
        )
        assert "beta staged" in beta_pane.query_one(ReviewDiffView).text


@pytest.mark.anyio
async def test_review_diff_bindings_move_cursor_cycle_tabs_and_jump_unstaged_edits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = next(
            pane for pane in review_tabs.query(TabPane) if pane._title == "alpha.py"
        )
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        viewer.focus()
        start = viewer.cursor_location

        await pilot.press("j")
        await pilot.pause(0)
        assert viewer.cursor_location[0] == start[0] + 1

        await pilot.press("k")
        await pilot.pause(0)
        assert viewer.cursor_location == start

        active_file = review_tabs.active
        await pilot.press("tab")
        await pilot.pause(0)
        assert review_tabs.active != active_file
        assert isinstance(app.screen.focused, ReviewDiffView)

        await pilot.press("shift+tab")
        await pilot.pause(0)
        assert review_tabs.active == active_file
        assert isinstance(app.screen.focused, ReviewDiffView)

        await pilot.press("]")
        await pilot.pause(0)
        assert viewer.cursor_location == (1, 0)

        await pilot.press("]")
        await pilot.pause(0)
        assert viewer.cursor_location == (2, 0)

        await pilot.press("]")
        await pilot.pause(0)
        assert viewer.cursor_location == (5, 0)

        deleted_strip = viewer.render_line(1)
        assert any(
            segment.style and segment.style.dim for segment in deleted_strip._segments
        )
        assert deleted_strip.crop(0, viewer.gutter_width).text.strip() == "-"
        added_strip = viewer.render_line(2)
        assert added_strip.crop(0, viewer.gutter_width).text.strip() == "+2"

        await pilot.press("[")
        await pilot.pause(0)
        assert viewer.cursor_location == (2, 0)

        await pilot.press("[")
        await pilot.pause(0)
        assert viewer.cursor_location == (1, 0)


@pytest.mark.anyio
async def test_review_grep_opens_modal_and_jumps_to_selected_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = next(
            pane for pane in review_tabs.query(TabPane) if pane._title == "alpha.py"
        )
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        viewer.focus()

        await pilot.press("@")
        await pilot.pause(0)
        modal = app.screen
        assert isinstance(modal, SearchProject)
        search_input = modal.query_one("#telescope-input")
        await pilot.click(search_input)
        await pilot.press("5", "0")
        await wait_for_condition(lambda: bool(modal.results))
        await pilot.press("enter")
        await pilot.pause(0)

        await wait_for_condition(lambda: app.screen is not modal)
        await wait_for_condition(lambda: viewer.cursor_location == (6, 0))

        assert app.query_one("#review-tabs", TabbedContent).active == (
            alpha_pane.id or ""
        )
        assert viewer.cursor_location == (6, 0)


@pytest.mark.anyio
async def test_review_focus_reloads_already_loaded_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = next(
            pane for pane in review_tabs.query(TabPane) if pane._title == "alpha.py"
        )
        beta_pane = next(
            pane for pane in review_tabs.query(TabPane) if pane._title == "beta.py"
        )
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        alpha_viewer = alpha_pane.query_one(ReviewDiffView)
        await wait_for_condition(lambda: bool(alpha_viewer.diff))
        assert "b = 20" in alpha_viewer.text

        review_tabs.active = beta_pane.id or ""
        await pilot.pause(0)
        (workspace / "alpha.py").write_text(
            "\n".join(["a = 1", "b = 200", "c = 3", "d = 4", "e = 50", "f = 6"]) + "\n",
            encoding="utf-8",
        )

        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)
        alpha_viewer.focus()
        await wait_for_condition(lambda: "b = 200" in alpha_viewer.text)

        assert "b = 200" in alpha_viewer.text


@pytest.mark.anyio
async def test_review_refresh_files_binding_reloads_unstaged_and_untracked_tabs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        assert {pane._title for pane in review_file_panes(review_tabs)} == {
            "alpha.py",
            "beta.py",
        }

        (workspace / "delta.py").write_text('value = "delta"\n', encoding="utf-8")

        await pilot.press("R")
        await wait_for_condition(
            lambda: any(
                pane._title == "delta.py"
                for pane in review_file_panes(
                    app.query_one("#review-tabs", TabbedContent)
                )
            )
        )

        assert {
            pane._title
            for pane in review_file_panes(app.query_one("#review-tabs", TabbedContent))
        } == {
            "alpha.py",
            "beta.py",
            "delta.py",
        }


@pytest.mark.anyio
async def test_review_show_file_adds_unmodified_file_tab_and_reuses_existing_tab(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = next(
            pane for pane in review_tabs.query(TabPane) if pane._title == "alpha.py"
        )
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        review = app.query_one(ReviewView)
        await review.open_file(Path("gamma.py"))
        await wait_for_condition(
            lambda: any(
                pane._title == "gamma.py"
                for pane in review_file_panes(
                    app.query_one("#review-tabs", TabbedContent)
                )
            )
        )
        review_tabs = app.query_one("#review-tabs", TabbedContent)
        gamma_pane = next(
            pane for pane in review_tabs.query(TabPane) if pane._title == "gamma.py"
        )
        await wait_for_condition(
            lambda: (
                app.query_one("#review-tabs", TabbedContent).active
                == (gamma_pane.id or "")
            )
        )
        gamma_viewer = gamma_pane.query_one(ReviewDiffView)
        await wait_for_condition(lambda: bool(gamma_viewer.diff))
        assert review_tabs.active == (gamma_pane.id or "")
        assert isinstance(app.screen.focused, ReviewDiffView)
        assert app.screen.focused.file_path == Path("gamma.py")
        assert 'value = "gamma"' in gamma_viewer.text

        pane_count = len(review_file_panes(review_tabs))
        await review.open_file(Path("alpha.py"))
        await wait_for_condition(
            lambda: (
                app.query_one("#review-tabs", TabbedContent).active
                == (alpha_pane.id or "")
            )
        )

        review_tabs = app.query_one("#review-tabs", TabbedContent)
        assert len(review_file_panes(review_tabs)) == pane_count
        alpha_pane = next(
            pane for pane in review_tabs.query(TabPane) if pane._title == "alpha.py"
        )
        assert review.active_pane is not None
        assert review.active_pane.file_path == Path("alpha.py")


@pytest.mark.anyio
async def test_review_refresh_binding_reloads_current_file_and_keeps_position(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test(size=(80, 24)) as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = next(
            pane for pane in review_tabs.query(TabPane) if pane._title == "alpha.py"
        )
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        await wait_for_condition(lambda: bool(viewer.diff))
        viewer.focus()
        viewer.move_cursor((5, 0))
        viewer.scroll_to(0, 2, animate=False, immediate=True)
        cursor = viewer.cursor_location
        scroll_offset = viewer.scroll_offset

        (workspace / "alpha.py").write_text(
            "\n".join(
                [
                    "a = 1",
                    "b = 20",
                    "c = 3",
                    "d = 4",
                    "e = 500",
                    "f = 6",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        await pilot.press("r")
        await wait_for_condition(lambda: "e = 500" in viewer.text)

        assert viewer.cursor_location == cursor
        assert viewer.scroll_offset == scroll_offset


@pytest.mark.anyio
async def test_review_stage_lines_updates_diff_and_shows_staged_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = next(
            pane for pane in review_tabs.query(TabPane) if pane._title == "alpha.py"
        )
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        viewer.focus()
        viewer.selection = type(viewer.selection)((1, 0), (3, 0))
        await viewer.action_review_stage_lines()
        await pilot.pause(0)

        assert any(
            line["is_staged"] and line["text"] == "b = 2"
            for line in get_diff(workspace / "alpha.py")
        )
        assert any(
            not line["is_staged"] and line["text"] == "e = 50"
            for line in get_diff(workspace / "alpha.py")
        )
        assert "b = 20" in viewer.text
        assert viewer.render_line(2).crop(0, viewer.gutter_width).text.strip() == "|2"
        assert viewer.text.count("a = 1") == 1
        assert viewer.selection.is_empty

        beta_pane = next(
            pane for pane in review_tabs.query(TabPane) if pane._title == "beta.py"
        )
        review_tabs.active = beta_pane.id or ""
        await pilot.pause(0)

        beta_viewer = beta_pane.query_one(ReviewDiffView)
        beta_viewer.focus()
        beta_viewer.selection = type(beta_viewer.selection)((0, 0), (2, 0))
        await beta_viewer.action_review_stage_lines()
        await pilot.pause(0)

        assert any(
            line["text"] == 'value = "beta staged"'
            for line in get_diff(workspace / "beta.py")
        )


@pytest.mark.anyio
async def test_review_adds_review_via_modal_and_submits_in_chat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)
    seen: list[str] = []

    async def fake_get_answer_streaming(
        *,
        session: sessions.Session,
        question: str,
        attachments=None,
    ):
        seen.append(question)
        if False:
            yield None

    monkeypatch.setattr(
        "faltoobot.faltoochat.app.sessions.get_answer_streaming",
        fake_get_answer_streaming,
    )

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = next(
            pane for pane in review_tabs.query(TabPane) if pane._title == "alpha.py"
        )
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        viewer.focus()
        viewer.move_cursor((1, 0), record_width=False)

        await pilot.press("a")
        await pilot.pause(0)
        modal = app.screen
        assert isinstance(modal, ReviewCommentModal)
        comment_input = modal.query_one("#review-comment-input", TextArea)
        await pilot.click(comment_input)
        await pilot.press(
            "L", "o", "o", "k", " ", "c", "l", "o", "s", "e", "l", "y", "enter"
        )
        await pilot.pause(0)

        assert app.query_one(ReviewView).reviews == [
            {
                "filename": Path("alpha.py"),
                "line_number_start": 2,
                "line_number_end": 2,
                "code": "b = 2",
                "comment": "Look closely",
            }
        ]

        await pilot.press("shift+enter")
        await wait_for_condition(lambda: bool(seen) and app.tabs().active == "chat-tab")
        await pilot.pause(0)

        assert "Look closely" in seen[0]
        assert "alpha.py" in seen[0]
        assert app.query_one(ReviewView).reviews == []


@pytest.mark.anyio
async def test_review_grep_modal_treats_results_as_plain_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from textual.app import App, ComposeResult
    from textual.widgets import Static

    monkeypatch.setattr(
        "faltoobot.faltoochat.widgets.search_project._project_search_results",
        lambda _workspace, _query: [
            {
                "title": 'alpha.py:1: BINDINGS = [Binding("escape", show=False)]',
                "path": Path("alpha.py"),
                "line_number": 1,
                "text": 'BINDINGS = [Binding("escape", show=False)]',
            }
        ],
    )

    class ModalApp(App[None]):
        def compose(self) -> ComposeResult:
            yield Static("ready")

    app = ModalApp()

    async with app.run_test() as pilot:
        app.push_screen(SearchProject(workspace=Path(".")))
        await pilot.pause(0)

        modal = app.screen
        assert isinstance(modal, SearchProject)
        search_input = modal.query_one("#telescope-input", Input)
        await pilot.click(search_input)
        await pilot.press("x")
        await pilot.pause(0)

        option_list = modal.query_one("#telescope-options", OptionList)
        assert len(option_list.options) == 1


@pytest.mark.anyio
async def test_telescope_up_and_down_bindings_move_highlight() -> None:
    from textual.app import App, ComposeResult
    from textual.widgets import Static

    class ModalApp(App[None]):
        def compose(self) -> ComposeResult:
            yield Static("ready")

    app = ModalApp()

    async with app.run_test() as pilot:
        app.push_screen(
            Telescope[Path](
                items=[Path("alpha.py"), Path("beta.py"), Path("gamma.py")],
                title="Open file in review",
                placeholder="Type a filename or path",
            )
        )
        await pilot.pause(0)

        modal = app.screen
        assert isinstance(modal, Telescope)
        option_list = modal.query_one("#telescope-options", OptionList)
        assert option_list.highlighted == 0

        await pilot.press("down")
        await pilot.pause(0)
        assert option_list.highlighted == 1

        await pilot.press("up")
        await pilot.pause(0)
        assert option_list.highlighted == 0


@pytest.mark.anyio
async def test_review_file_modal_uses_option_index_for_selection() -> None:
    from textual.app import App, ComposeResult
    from textual.widgets import Static

    selected: list[Path | None] = []

    class ModalApp(App[None]):
        def compose(self) -> ComposeResult:
            yield Static("ready")

    app = ModalApp()

    async with app.run_test() as pilot:
        app.push_screen(
            Telescope[Path](
                items=[Path("alpha.py"), Path("beta.py")],
                title="Open file in review",
                placeholder="Type a filename or path",
            ),
            selected.append,
        )
        await pilot.pause(0)

        modal = app.screen
        assert isinstance(modal, Telescope)
        option_list = modal.query_one("#telescope-options", OptionList)
        modal.on_option_list_option_selected(
            OptionList.OptionSelected(
                option_list,
                Option("beta.py"),
                1,
            )
        )
        await pilot.pause(0)

        assert selected == [Path("beta.py")]


@pytest.mark.anyio
async def test_review_modal_treats_code_as_plain_text() -> None:
    from textual.app import App, ComposeResult
    from textual.widgets import Static

    class ModalApp(App[None]):
        def compose(self) -> ComposeResult:
            yield Static("ready")

    app = ModalApp()

    async with app.run_test() as pilot:
        app.push_screen(
            ReviewCommentModal(
                Path("gamma.py"),
                1,
                2,
                "items: list[Review] = []\ncheck=False),\n",
            )
        )
        await pilot.pause(0)
        assert isinstance(app.screen, ReviewCommentModal)


@pytest.mark.anyio
async def test_review_add_uses_selected_lines_and_allows_unmodified_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = next(
            pane for pane in review_tabs.query(TabPane) if pane._title == "alpha.py"
        )
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        viewer.focus()
        viewer.move_cursor((0, 0), record_width=False)

        await pilot.press("a")
        await pilot.pause(0)
        modal = app.screen
        assert isinstance(modal, ReviewCommentModal)
        comment_input = modal.query_one("#review-comment-input", TextArea)
        await pilot.click(comment_input)
        await pilot.press("U", "n", "c", "h", "a", "n", "g", "e", "d", "enter")
        await pilot.pause(0)

        assert app.query_one(ReviewView).reviews[-1] == {
            "filename": Path("alpha.py"),
            "line_number_start": 1,
            "line_number_end": 1,
            "code": "a = 1",
            "comment": "Unchanged",
        }

        viewer.selection = type(viewer.selection)((1, 0), (4, 0))
        await pilot.press("a")
        await pilot.pause(0)
        modal = app.screen
        assert isinstance(modal, ReviewCommentModal)
        comment_input = modal.query_one("#review-comment-input", TextArea)
        await pilot.click(comment_input)
        await pilot.press("S", "e", "l", "e", "c", "t", "e", "d", "enter")
        await pilot.pause(0)

        assert app.query_one(ReviewView).reviews[-1] == {
            "filename": Path("alpha.py"),
            "line_number_start": 2,
            "line_number_end": 4,
            "code": "b = 2\nb = 20\nc = 3",
            "comment": "Selected",
        }


def test_review_tab_titles_use_filenames_when_unique() -> None:
    titles = _review_tab_titles(
        [
            Path("src/app.py"),
            Path("tests/test_app.py"),
            Path("README.md"),
        ]
    )

    assert titles == {
        Path("src/app.py"): "app.py",
        Path("tests/test_app.py"): "test_app.py",
        Path("README.md"): "README.md",
    }


def test_review_tab_titles_keep_paths_for_duplicate_names() -> None:
    titles = _review_tab_titles(
        [
            Path("src/app.py"),
            Path("tests/app.py"),
        ]
    )

    assert titles == {
        Path("src/app.py"): "src/app.py",
        Path("tests/app.py"): "tests/app.py",
    }


@pytest.mark.anyio
async def test_review_add_prefills_existing_comment_and_overwrites_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = next(
            pane for pane in review_tabs.query(TabPane) if pane._title == "alpha.py"
        )
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        viewer.focus()
        viewer.move_cursor((1, 0), record_width=False)

        await pilot.press("a")
        await pilot.pause(0)
        modal = app.screen
        assert isinstance(modal, ReviewCommentModal)
        comment_input = modal.query_one("#review-comment-input", TextArea)
        await pilot.click(comment_input)
        await pilot.press("F", "i", "r", "s", "t", "enter")
        await pilot.pause(0)

        assert app.query_one(ReviewView).reviews[-1]["comment"] == "First"
        assert viewer.render_line(1).crop(0, viewer.gutter_width).text.strip() == "*"
        assert viewer.border_title == "1 comment"

        await pilot.press("a")
        await pilot.pause(0)
        modal = app.screen
        assert isinstance(modal, ReviewCommentModal)
        comment_input = modal.query_one("#review-comment-input", TextArea)
        assert comment_input.text == "First"
        comment_input.load_text("")
        await pilot.click(comment_input)
        await pilot.press("S", "e", "c", "o", "n", "d", "enter")
        await pilot.pause(0)

        assert app.query_one(ReviewView).reviews == [
            {
                "filename": Path("alpha.py"),
                "line_number_start": 2,
                "line_number_end": 2,
                "code": "b = 2",
                "comment": "Second",
            }
        ]


@pytest.mark.anyio
async def test_review_blank_comment_deletes_existing_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = next(
            pane for pane in review_tabs.query(TabPane) if pane._title == "alpha.py"
        )
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        viewer.focus()
        viewer.move_cursor((1, 0), record_width=False)

        await pilot.press("a")
        await pilot.pause(0)
        modal = app.screen
        assert isinstance(modal, ReviewCommentModal)
        comment_input = modal.query_one("#review-comment-input", TextArea)
        await pilot.click(comment_input)
        await pilot.press("F", "i", "r", "s", "t", "enter")
        await pilot.pause(0)

        assert len(app.query_one(ReviewView).reviews) == 1
        assert viewer.border_title == "1 comment"

        await pilot.press("a")
        await pilot.pause(0)
        modal = app.screen
        assert isinstance(modal, ReviewCommentModal)
        comment_input = modal.query_one("#review-comment-input", TextArea)
        assert comment_input.text == "First"
        comment_input.load_text("")
        await pilot.click(comment_input)
        await pilot.press("enter")
        await pilot.pause(0)

        assert app.query_one(ReviewView).reviews == []
        assert viewer.render_line(1).crop(0, viewer.gutter_width).text.strip() != "*"
        assert viewer.border_title == "0 comments"


@pytest.mark.anyio
async def test_review_modal_supports_multiline_comments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = next(
            pane for pane in review_tabs.query(TabPane) if pane._title == "alpha.py"
        )
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        viewer.focus()
        viewer.move_cursor((1, 0), record_width=False)

        await pilot.press("a")
        await pilot.pause(0)
        modal = app.screen
        assert isinstance(modal, ReviewCommentModal)
        comment_input = modal.query_one("#review-comment-input", TextArea)
        await pilot.click(comment_input)
        await pilot.press("L", "i", "n", "e", "1")
        await pilot.press("shift+enter")
        await pilot.press("L", "i", "n", "e", "2")
        await pilot.press("enter")
        await pilot.pause(0)

        assert app.query_one(ReviewView).reviews[-1]["comment"] == "Line1\nLine2"


@pytest.mark.anyio
async def test_review_footer_bindings_follow_search_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = next(
            pane for pane in review_tabs.query(TabPane) if pane._title == "alpha.py"
        )
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        viewer.focus()
        await pilot.pause(0)

        bindings = app.screen.active_bindings
        descriptions = {binding.binding.description for binding in bindings.values()}
        assert "Next Edit" in descriptions
        assert "Prev Edit" in descriptions
        assert "Next Search" in descriptions
        assert "Prev Search" in descriptions
        assert "Search" in descriptions
        assert bindings["escape"].binding.description == "Leave Search"

        await pilot.press("/")
        await pilot.pause(0)
        search_modal = app.screen
        search_input = search_modal.query_one("#review-search-input")
        await pilot.click(search_input)
        await pilot.press("5", "0", "enter")
        await pilot.pause(0)

        bindings = app.screen.active_bindings
        descriptions = {binding.binding.description for binding in bindings.values()}
        assert "Next Edit" in descriptions
        assert "Prev Edit" in descriptions
        assert "Next Search" in descriptions
        assert "Prev Search" in descriptions
        assert bindings["escape"].binding.description == "Leave Search"

        await pilot.press("escape")
        await pilot.pause(0)

        bindings = app.screen.active_bindings
        descriptions = {binding.binding.description for binding in bindings.values()}
        assert "Next Edit" in descriptions
        assert "Prev Edit" in descriptions
        assert "Next Search" in descriptions
        assert "Prev Search" in descriptions
        assert bindings["escape"].binding.description == "Leave Search"


@pytest.mark.anyio
async def test_review_search_mode_jumps_by_search_and_escape_resets_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = next(
            pane for pane in review_tabs.query(TabPane) if pane._title == "alpha.py"
        )
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        viewer.focus()

        await pilot.press("/")
        await pilot.pause(0)
        search_modal = app.screen
        search_input = search_modal.query_one("#review-search-input")
        await pilot.click(search_input)
        await pilot.press("5", "0", "enter")
        await pilot.pause(0)

        assert app.query_one(ReviewView).search_term == "50"
        assert viewer.cursor_location == (6, 4)

        await pilot.press("n")
        await pilot.pause(0)
        assert viewer.cursor_location == (6, 4)

        await pilot.press("escape")
        await pilot.pause(0)

        assert app.query_one(ReviewView).search_term == ""
