import asyncio
from contextlib import nullcontext
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, OptionList, Static, TabbedContent, TabPane, TextArea
from textual.widgets.option_list import Option

from faltoobot import sessions
from faltoobot.faltoochat.app import FaltooChatApp
from faltoobot.faltoochat.diff import get_diff
from faltoobot.faltoochat.review import (
    ReviewView,
    _get_modified_files,
    _review_tab_titles,
)
from faltoobot.faltoochat.widgets import (
    ReviewCommentModal,
    ReviewDiffView,
    SearchProject,
    Telescope,
)
from faltoobot.faltoochat.widgets.search_file import SearchFile as SearchFileModal

EXPECTED_REVIEW_FILES = 2
EXPECTED_SPLIT_VIEWERS = 2


def test_review_missing_workspace_returns_git_error(tmp_path: Path) -> None:
    workspace = tmp_path / "missing"

    assert _get_modified_files(workspace) == ("Git repository not found.", [])


def review_file_panes(tabs: TabbedContent) -> list[TabPane]:
    return [pane for pane in tabs.query(TabPane) if pane.id != "no-changes"]


def review_file_titles(tabs: TabbedContent) -> set[str]:
    return {str(pane._title) for pane in review_file_panes(tabs)}


def review_pane(tabs: TabbedContent, title: str) -> TabPane:
    return next(pane for pane in tabs.query(TabPane) if pane._title == title)


def active_review_title(tabs: TabbedContent) -> str:
    return next(
        str(pane._title) for pane in review_file_panes(tabs) if pane.id == tabs.active
    )


async def wait_for_condition(check, *, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if check():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was not met before timeout")


async def wait_for_workers(app: FaltooChatApp, pilot) -> None:
    await pilot.pause(0)
    await app.workers.wait_for_complete()
    await pilot.pause(0)


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
    await wait_for_condition(
        lambda: bool(app.query_one("#review-tabs", TabbedContent).query(TabPane))
    )
    await app.query_one(ReviewView).refresh_files()
    await pilot.pause(0)
    return app.query_one("#review-tabs", TabbedContent)


def assert_review_search_bindings(app: FaltooChatApp) -> None:
    bindings = app.screen.active_bindings
    descriptions = {binding.binding.description for binding in bindings.values()}
    assert "Next Change" in descriptions
    assert "Previous Change" in descriptions
    assert "Next Match" in descriptions
    assert "Previous Match" in descriptions
    assert "Search File" in descriptions
    assert bindings["escape"].binding.description == "Exit Search"


@pytest.mark.anyio
async def test_review_search_project_opens_with_no_modified_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, app = build_app(tmp_path, monkeypatch)

    async with app.run_test() as pilot:
        await open_review(app, pilot)
        await pilot.press("@")
        await pilot.pause(0)
        assert isinstance(app.screen, SearchProject)


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
        assert review_file_titles(review_tabs) == {"alpha.py"}


@pytest.mark.anyio
async def test_review_diff_bindings_move_cursor_cycle_tabs_and_jump_unstaged_edits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = review_pane(review_tabs, "alpha.py")
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
        assert viewer.cursor_location == (1, 0)

        await pilot.press("[")
        await pilot.pause(0)
        assert viewer.cursor_location == (5, 0)

        assert viewer.soft_wrap is True


@pytest.mark.anyio
async def test_review_ctrl_d_opens_editor_and_refreshes_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)
    seen: list[tuple[Path, int | None]] = []

    def fake_open_in_editor(
        path: Path,
        *,
        line_number: int | None = None,
    ) -> bool:
        seen.append((path, line_number))
        path.write_text(
            "\n".join(
                [
                    "a = 1",
                    "b = 200",
                    "c = 3",
                    "d = 4",
                    "e = 50",
                    "f = 6",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return True

    monkeypatch.setattr(
        "faltoobot.faltoochat.widgets.review_diff.open_in_editor",
        fake_open_in_editor,
    )
    monkeypatch.setattr(app, "suspend", lambda: nullcontext())

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = review_pane(review_tabs, "alpha.py")
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        viewer.focus()
        viewer.move_cursor((1, 0), record_width=False)

        await pilot.press("ctrl+d")
        await wait_for_condition(lambda: "b = 200" in viewer.text)
        await pilot.pause(0)

        assert seen == [(workspace / "alpha.py", 2)]
        assert "b = 200" in viewer.text


@pytest.mark.anyio
async def test_review_diff_defaults_to_wrap_and_highlight_toggle_applies_app_wide(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = review_pane(review_tabs, "alpha.py")
        beta_pane = review_pane(review_tabs, "beta.py")
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        alpha_viewer = alpha_pane.query_one(ReviewDiffView)
        beta_viewer = beta_pane.query_one(ReviewDiffView)
        alpha_viewer.focus()
        await pilot.pause(0)

        assert alpha_viewer.soft_wrap is True
        assert beta_viewer.soft_wrap is True
        assert alpha_viewer.line_highlights is True
        assert beta_viewer.line_highlights is True

        await pilot.press("H")
        await pilot.pause(0)

        assert alpha_viewer.soft_wrap is True
        assert beta_viewer.soft_wrap is True
        assert alpha_viewer.line_highlights is False
        assert beta_viewer.line_highlights is False

        await pilot.press("H")
        await pilot.pause(0)

        assert alpha_viewer.line_highlights is True
        assert beta_viewer.line_highlights is True

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
        gamma_pane = review_pane(
            app.query_one("#review-tabs", TabbedContent), "gamma.py"
        )
        await wait_for_workers(app, pilot)
        gamma_viewer = gamma_pane.query_one(ReviewDiffView)
        assert gamma_viewer.soft_wrap is True
        assert gamma_viewer.line_highlights is True


@pytest.mark.anyio
async def test_review_diff_updates_theme_colors_when_app_theme_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    alpha = workspace / "alpha.py"
    alpha.write_text('value = "alpha"\n', encoding="utf-8")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "initial")
    alpha.write_text('value = "beta"\n', encoding="utf-8")

    async with app.run_test() as pilot:
        app.theme = "textual-dark"
        await pilot.pause(0)

        review_tabs = await open_review(app, pilot)
        alpha_pane = review_pane(review_tabs, "alpha.py")
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        viewer.focus()
        await pilot.pause(0)

        before_color = (
            viewer._theme.base_style.color
            if viewer._theme and viewer._theme.base_style
            else None
        )

        app.theme = "textual-light"
        await pilot.pause(0)

        assert viewer.theme == "faltoobot_review_light"
        assert viewer._theme is not None
        assert viewer._theme.base_style is not None
        assert viewer._theme.base_style.color != before_color


@pytest.mark.anyio
async def test_review_diff_highlights_tint_rendered_line_background(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = review_pane(review_tabs, "alpha.py")
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        viewer.focus()
        await pilot.pause(0)

        before = [
            segment.style.bgcolor if segment.style else None
            for segment in viewer.render_line(2).crop(viewer.gutter_width, 80)._segments
        ]
        gutter_before = [
            segment.style.bgcolor if segment.style else None
            for segment in viewer.render_line(2).crop(0, viewer.gutter_width)._segments
        ]

        await pilot.press("H")
        await pilot.pause(0)

        after = [
            segment.style.bgcolor if segment.style else None
            for segment in viewer.render_line(2).crop(viewer.gutter_width, 80)._segments
        ]

        assert before != after
        assert gutter_before
        assert before
        assert max(gutter_before, key=gutter_before.count) == max(
            before, key=before.count
        )


@pytest.mark.anyio
async def test_review_multiline_selection_keeps_gutter_and_padding_highlights(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    alpha = workspace / "alpha.md"
    alpha.write_text(
        "first line\nsecond line\nthird line\n",
        encoding="utf-8",
    )
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "initial")
    alpha.write_text(
        "first line\nsecond line with enough text to leave only a little padding\nthird line\n",
        encoding="utf-8",
    )

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = next(
            pane for pane in review_tabs.query(TabPane) if pane._title == "alpha.md"
        )
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        viewer.focus()
        before_gutter = [
            segment.style.bgcolor if segment.style else None
            for segment in viewer.render_line(1).crop(0, viewer.gutter_width)._segments
        ]
        before_body = [
            segment.style.bgcolor if segment.style else None
            for segment in viewer.render_line(1).crop(viewer.gutter_width, 80)._segments
        ]

        viewer.selection = type(viewer.selection)(
            (0, 0),
            (2, len(viewer.document.get_line(2))),
        )
        await pilot.pause(0)

        selection_bg = (
            viewer._theme.selection_style.bgcolor
            if viewer._theme and viewer._theme.selection_style
            else None
        )
        after_gutter = [
            segment.style.bgcolor if segment.style else None
            for segment in viewer.render_line(1).crop(0, viewer.gutter_width)._segments
        ]
        after_body = [
            segment.style.bgcolor if segment.style else None
            for segment in viewer.render_line(1).crop(viewer.gutter_width, 80)._segments
        ]

        assert selection_bg in after_body
        assert after_gutter == before_gutter
        assert after_body[-1] == before_body[-1]


@pytest.mark.anyio
async def test_review_diff_highlights_cover_empty_added_line_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    alpha = workspace / "alpha.py"
    alpha.write_text("start\nend\n", encoding="utf-8")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "initial")
    alpha.write_text("start\n\nend\n", encoding="utf-8")

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = review_pane(review_tabs, "alpha.py")
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        viewer.focus()
        blank_diff_line = next(
            index
            for index, line in enumerate(viewer.diff)
            if line["type"] == "+" and line["text"] == ""
        )
        blank_display_line = viewer._display_line(blank_diff_line)
        viewer.move_cursor((blank_display_line, 0), record_width=False)
        await pilot.pause(0)

        gutter = [
            segment.style.bgcolor if segment.style else None
            for segment in viewer.render_line(blank_display_line)
            .crop(0, viewer.gutter_width)
            ._segments
        ]
        body = [
            segment.style.bgcolor if segment.style else None
            for segment in viewer.render_line(blank_display_line)
            .crop(viewer.gutter_width, 80)
            ._segments
        ]

        assert gutter
        assert body
        assert body[-1] == gutter[0]


@pytest.mark.anyio
async def test_review_diff_highlights_keep_cursor_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = review_pane(review_tabs, "alpha.py")
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        viewer.focus()
        viewer.move_cursor((2, 2), record_width=False)
        await pilot.pause(0)

        await pilot.press("H")
        await pilot.pause(0)

        before = [
            segment.style.bgcolor if segment.style else None
            for segment in viewer.render_line(2).crop(viewer.gutter_width, 80)._segments
        ]
        before_line_bg = max(before, key=before.count)
        cursor_bg = next(bg for bg in before if bg != before_line_bg)

        await pilot.press("H")
        await pilot.pause(0)

        after = [
            segment.style.bgcolor if segment.style else None
            for segment in viewer.render_line(2).crop(viewer.gutter_width, 80)._segments
        ]

        assert cursor_bg in after


@pytest.mark.anyio
async def test_review_visual_line_selection_extends_with_j_and_k(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = review_pane(review_tabs, "alpha.py")
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        viewer.focus()
        await pilot.press("j", "j")
        await pilot.pause(0)
        line = viewer.cursor_location[0]

        await pilot.press("V")
        await pilot.pause(0)
        assert viewer.selection.start == (line, 0)
        assert viewer.selection.end == (line, len(viewer.document.get_line(line)))

        await pilot.press("k")
        await pilot.pause(0)
        assert viewer.cursor_location == (line - 1, 0)
        assert viewer.selection.start == (line, len(viewer.document.get_line(line)))
        assert viewer.selection.end == (line - 1, 0)

        cursor = viewer.cursor_location
        await pilot.press("escape")
        await pilot.pause(0)
        assert viewer.selection.is_empty
        assert viewer.cursor_location == cursor

        await pilot.press("V")
        await pilot.pause(0)
        assert viewer.selection.start == (cursor[0], 0)
        assert viewer.selection.end == (
            cursor[0],
            len(viewer.document.get_line(cursor[0])),
        )

        await pilot.press("j")
        await pilot.pause(0)
        next_line = cursor[0] + 1
        assert viewer.selection.start == (cursor[0], 0)
        assert viewer.selection.end == (
            next_line,
            len(viewer.document.get_line(next_line)),
        )
        assert viewer.cursor_location == viewer.selection.end


@pytest.mark.anyio
async def test_review_modal_still_closes_after_switching_tabs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        await open_review(app, pilot)
        await pilot.press("@")
        await pilot.pause(0)
        assert isinstance(app.screen, SearchProject)

        await pilot.press("ctrl+1")
        await pilot.pause(0)
        await pilot.press("ctrl+2")
        await pilot.pause(0)
        await pilot.press("escape")
        await pilot.pause(0)

        assert not isinstance(app.screen, SearchProject)


@pytest.mark.anyio
async def test_review_grep_opens_modal_and_jumps_to_selected_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = review_pane(review_tabs, "alpha.py")
        beta_pane = review_pane(review_tabs, "beta.py")
        review_tabs.active = beta_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        beta_pane.query_one(ReviewDiffView).focus()
        target_line = 5

        await pilot.press("@")
        await pilot.pause(0)
        modal = app.screen
        assert isinstance(modal, SearchProject)
        search_input = modal.query_one("#telescope-input")
        await pilot.click(search_input)
        await pilot.press("5", "0")
        await wait_for_condition(
            lambda: (
                bool(modal.results)
                and modal.results[0]["line_number"] is not None
                and modal.results[0]["line_number"] == target_line
            )
        )
        await pilot.press("enter")
        await pilot.pause(0)

        await wait_for_condition(lambda: app.screen is not modal)
        await wait_for_workers(app, pilot)
        await wait_for_condition(lambda: viewer.cursor_location == (6, 0))

        assert app.query_one("#review-tabs", TabbedContent).active == (
            alpha_pane.id or ""
        )
        assert viewer.cursor_location == (6, 0)


@pytest.mark.anyio
async def test_review_open_split_search_loads_selected_file_in_right_pane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = review_pane(review_tabs, "alpha.py")
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        viewer.focus()
        await pilot.press("O")
        await pilot.pause(0)

        modal = app.screen
        assert isinstance(modal, SearchProject)
        search_input = modal.query_one("#telescope-input")
        await pilot.click(search_input)
        await pilot.press("b", "e", "t", "a")
        await wait_for_condition(
            lambda: bool(modal.results) and modal.results[0]["path"] == Path("beta.py")
        )
        await pilot.press("enter")

        await wait_for_condition(lambda: app.screen is not modal)
        await wait_for_condition(
            lambda: len(alpha_pane.query(ReviewDiffView)) == EXPECTED_SPLIT_VIEWERS
        )
        viewers = list(alpha_pane.query(ReviewDiffView))
        right_viewer = viewers[-1]

        assert right_viewer.file_path == Path("beta.py")
        assert app.query_one(ReviewView).active_pane is right_viewer

        await pilot.press("q")
        await pilot.pause(0)

        assert list(alpha_pane.query(ReviewDiffView)) == [viewer]
        assert app.query_one(ReviewView).active_pane is viewer


@pytest.mark.anyio
async def test_review_refresh_files_binding_reloads_current_tab_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = review_pane(review_tabs, "alpha.py")
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        alpha_viewer = alpha_pane.query_one(ReviewDiffView)
        alpha_viewer.focus()
        await wait_for_condition(lambda: bool(alpha_viewer.diff))
        assert "b = 20" in alpha_viewer.text

        (workspace / "alpha.py").write_text(
            "\n".join(["a = 1", "b = 200", "c = 3", "d = 4", "e = 50", "f = 6"]) + "\n",
            encoding="utf-8",
        )

        await pilot.press("R")
        await wait_for_condition(lambda: "b = 200" in alpha_viewer.text)

        assert app.screen.focused is alpha_viewer
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
        assert review_file_titles(review_tabs) == {
            "alpha.py",
            "beta.py",
        }

        review = app.query_one(ReviewView)
        await review.open_file(Path("gamma.py"))
        await pilot.pause(0)
        assert review_file_titles(review_tabs) == {
            "alpha.py",
            "beta.py",
            "gamma.py",
        }

        gamma_pane = next(
            pane for pane in review_tabs.query(TabPane) if pane._title == "gamma.py"
        )
        gamma_viewer = gamma_pane.query_one(ReviewDiffView)
        gamma_viewer.focus()
        await pilot.pause(0)
        assert app.screen.focused is gamma_viewer

        (workspace / "delta.py").write_text('value = "delta"\n', encoding="utf-8")

        await pilot.press("R")
        await wait_for_condition(
            lambda: (
                {
                    pane._title
                    for pane in review_file_panes(
                        app.query_one("#review-tabs", TabbedContent)
                    )
                }
                == {"alpha.py", "beta.py", "delta.py"}
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
        alpha_pane = review_pane(review_tabs, "alpha.py")
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
        alpha_pane = review_pane(review_tabs, "alpha.py")
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
        alpha_pane = review_pane(review_tabs, "alpha.py")
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
async def test_review_stage_file_stages_current_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = review_pane(review_tabs, "alpha.py")
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        viewer.focus()
        await viewer.action_review_stage_file()
        await pilot.pause(0)

        diff = get_diff(workspace / "alpha.py")
        assert any(line["is_staged"] for line in diff if line["type"] in {"+", "-"})
        assert not any(
            not line["is_staged"] for line in diff if line["type"] in {"+", "-"}
        )
        assert viewer.selection.is_empty
        assert review_file_titles(review_tabs) == {"beta.py"}


@pytest.mark.anyio
async def test_review_adds_review_via_modal_and_submits_in_chat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)
    seen: list[str] = []

    async def fake_get_answer_streaming(session: sessions.Session):
        seen.append(str(sessions.get_messages(session)["messages"][-1]["content"]))
        if False:
            yield None

    monkeypatch.setattr(
        "faltoobot.faltoochat.app.sessions.get_answer_streaming",
        fake_get_answer_streaming,
    )

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = review_pane(review_tabs, "alpha.py")
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
                "file_line_number_start": 2,
                "file_line_number_end": 2,
                "code": "-b = 2",
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
async def test_search_project_warns_when_rg_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    seen: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "faltoobot.faltoochat.widgets.search_project.shutil.which",
        lambda _name: None,
    )

    class ModalApp(App[None]):
        def compose(self) -> ComposeResult:
            yield Static("ready")

    app = ModalApp()

    def fake_notify(
        message: str,
        *,
        title: str = "",
        severity: str = "information",
        timeout: int | float | None = None,
        markup: bool = True,
    ) -> None:
        seen.append((message, severity))

    app.notify = cast(Any, fake_notify)

    async with app.run_test() as pilot:
        app.push_screen(SearchProject(workspace=Path(".")))
        await pilot.pause(0)

    assert seen == [("Install ripgrep (`rg`) to search project files.", "warning")]


@pytest.mark.anyio
async def test_review_grep_modal_treats_results_as_plain_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.setattr(
        "faltoobot.faltoochat.widgets.search_project._project_search_results",
        lambda _workspace, _query, **_kwargs: [
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
        await wait_for_condition(
            lambda: len(modal.query_one("#telescope-options", OptionList).options) == 1
        )

        option_list = modal.query_one("#telescope-options", OptionList)
        assert len(option_list.options) == 1


@pytest.mark.anyio
async def test_telescope_debounces_callable_searches() -> None:

    seen: list[str] = []

    class ModalApp(App[None]):
        def compose(self) -> ComposeResult:
            yield Static("ready")

    app = ModalApp()

    async with app.run_test() as pilot:
        app.push_screen(
            Telescope[str](
                items=lambda query: seen.append(query) or [query],
                title="Search File",
                placeholder="Type",
            )
        )
        await pilot.pause(0.05)

        modal = app.screen
        assert isinstance(modal, Telescope)
        search_input = modal.query_one("#telescope-input", Input)
        await pilot.click(search_input)
        await pilot.press("a", "b")
        await pilot.pause(0.3)

        assert seen[0] == ""
        assert seen[-1] == "ab"
        assert "a" not in seen[1:-1]


@pytest.mark.anyio
async def test_telescope_up_and_down_bindings_move_highlight() -> None:

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
async def test_review_modal_keeps_long_code_scrollable() -> None:
    from textual.containers import VerticalScroll

    class ModalApp(App[None]):
        def compose(self) -> ComposeResult:
            yield Static("ready")

    app = ModalApp()
    code = "\n".join(f"line {index}" for index in range(40))

    async with app.run_test() as pilot:
        app.push_screen(ReviewCommentModal(Path("gamma.py"), 1, 40, code))
        await pilot.pause(0)

        modal = app.screen
        assert isinstance(modal, ReviewCommentModal)
        code_scroll = modal.query_one("#review-comment-code-scroll", VerticalScroll)
        dialog = modal.query_one("#review-comment-dialog")
        comment_input = modal.query_one("#review-comment-input", TextArea)
        assert dialog.outer_size.height == min(
            modal.size.height - 4, round(modal.size.width * 2 / 3)
        )
        assert code_scroll.outer_size.height > comment_input.outer_size.height


@pytest.mark.anyio
async def test_review_modal_treats_code_as_plain_text() -> None:

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
async def test_review_add_includes_empty_line_when_selection_ends_at_line_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    sample = workspace / "sample.py"
    sample.write_text("one = 1\nthree = 3\n", encoding="utf-8")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "initial")
    sample.write_text("one = 1\n\nthree = 3\n", encoding="utf-8")

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        sample_pane = next(
            pane for pane in review_tabs.query(TabPane) if pane._title == "sample.py"
        )
        review_tabs.active = sample_pane.id or ""
        await pilot.pause(0)

        viewer = sample_pane.query_one(ReviewDiffView)
        viewer.focus()
        viewer.selection = type(viewer.selection)((0, 0), (1, 0))

        await pilot.press("a")
        await pilot.pause(0)
        modal = app.screen
        assert isinstance(modal, ReviewCommentModal)
        assert (modal.line_number_start, modal.line_number_end) == (1, 2)
        assert modal.code == "one = 1\n+"


@pytest.mark.anyio
async def test_review_add_uses_selected_lines_and_allows_unmodified_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = review_pane(review_tabs, "alpha.py")
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
            "file_line_number_start": 1,
            "file_line_number_end": 1,
            "code": "a = 1",
            "comment": "Unchanged",
        }

        viewer.move_cursor((1, 0), record_width=False)
        await pilot.press("V", "j")
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
            "line_number_end": 3,
            "file_line_number_start": 2,
            "file_line_number_end": 2,
            "code": "-b = 2\n+b = 20",
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
async def test_review_modal_supports_multiline_comments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = review_pane(review_tabs, "alpha.py")
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
async def test_review_comment_at_opens_file_picker_and_inserts_mention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = review_pane(review_tabs, "alpha.py")
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

        await pilot.press("@")
        await pilot.pause(0)
        picker = app.screen
        assert isinstance(picker, SearchFileModal)
        search_input = picker.query_one("#telescope-input", Input)
        await pilot.click(search_input)
        await pilot.press("b", "e", "t", "a")
        await wait_for_condition(
            lambda: len(picker.query_one("#telescope-options", OptionList).options) == 1
        )
        await pilot.press("enter")
        await wait_for_condition(
            lambda: (
                isinstance(app.screen, ReviewCommentModal)
                and app.screen.query_one("#review-comment-input", TextArea).text
                == "`beta.py` "
            )
        )

        assert (
            app.screen.query_one("#review-comment-input", TextArea).text == "`beta.py` "
        )


@pytest.mark.anyio
async def test_review_comment_cancelled_at_picker_inserts_at(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = review_pane(review_tabs, "alpha.py")
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

        await pilot.press("@")
        await pilot.pause(0)
        assert isinstance(app.screen, SearchFileModal)

        await pilot.press("escape")
        await wait_for_condition(
            lambda: (
                isinstance(app.screen, ReviewCommentModal)
                and app.screen.query_one("#review-comment-input", TextArea).text == "@"
            )
        )

        assert comment_input.text == "@"


@pytest.mark.anyio
async def test_review_search_mode_jumps_by_search_and_escape_resets_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    create_modified_files(workspace)

    async with app.run_test() as pilot:
        review_tabs = await open_review(app, pilot)
        alpha_pane = review_pane(review_tabs, "alpha.py")
        review_tabs.active = alpha_pane.id or ""
        await pilot.pause(0)

        viewer = alpha_pane.query_one(ReviewDiffView)
        viewer.focus()
        await pilot.pause(0)

        assert_review_search_bindings(app)

        await pilot.press("/")
        await pilot.pause(0)
        search_modal = app.screen
        search_input = search_modal.query_one("#text-input-input")
        await pilot.click(search_input)
        await pilot.press("5", "0", "enter")
        await pilot.pause(0)

        assert app.query_one(ReviewView).search_term == "50"
        assert viewer.cursor_location == (6, 4)
        assert_review_search_bindings(app)

        await pilot.press("n")
        await pilot.pause(0)
        assert viewer.cursor_location == (6, 4)

        await pilot.press("escape")
        await pilot.pause(0)

        assert app.query_one(ReviewView).search_term == ""
        assert_review_search_bindings(app)
