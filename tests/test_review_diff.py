import pytest
from textual.app import App, ComposeResult
from textual.color import Color
import subprocess
from pathlib import Path
from typing import Any, cast

from faltoobot.faltoochat.diff import Diff, get_diff
from faltoobot.faltoochat.git import (
    _selected_patch,
    _stage_entries,
    apply_selected_diff_lines,
    get_unstaged_files,
)
from faltoobot.faltoochat.editor_utils import (
    next_modification,
    next_search_line,
    previous_modification,
    word_under_cursor,
)
from faltoobot.faltoochat.widgets.review_diff import (
    ReviewDiffView,
    _line_highlight_style,
    _review_range,
)


EXPECTED_GUTTER_WIDTH = 6


class ReviewDiffApp(App[None]):
    CSS = """
    ReviewDiffView {
        width: 40;
        height: 10;
    }
    """

    def __init__(self, viewer: ReviewDiffView) -> None:
        super().__init__()
        self.viewer = viewer

    def compose(self) -> ComposeResult:
        yield self.viewer


def _set_theme_colors(monkeypatch) -> dict[str, Color]:
    colors = {
        "error_light": Color.parse("#b56f78").lighten(0.30),
        "success_light": Color.parse("#6fa06f").lighten(0.30),
        "primary_light": Color.parse("#6f8fb8").lighten(0.30),
        "secondary_light": Color.parse("#4a6fa5").lighten(0.30),
    }

    class ThemeStub:
        error = "#b56f78"
        success = "#6fa06f"
        primary = "#6f8fb8"
        secondary = "#4a6fa5"
        luminosity_spread = 0.15

    class AppStub:
        current_theme = ThemeStub()

    monkeypatch.setattr(ReviewDiffView, "app", property(lambda self: AppStub()))
    return colors


class ReviewViewStub:
    def __init__(self) -> None:
        self.active_pane = None
        self.reviews = []
        self.search_term = ""
        self.search_whole_word = False

    def add_review(self, _review) -> None:
        return

    async def submit_reviews(self) -> None:
        return


def review_view_stub() -> ReviewViewStub:
    return ReviewViewStub()


def git(workspace: Path, *args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=workspace,
        input=input_text,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_selected_patch_stages_insertions_at_the_right_location(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    git(workspace, "init")
    git(workspace, "config", "user.email", "tests@example.com")
    git(workspace, "config", "user.name", "Tests")

    file_path = workspace / "alpha.py"
    file_path.write_text(
        'import1\n\nclass A:\n    CSS = """\n    App {\n    }\n',
        encoding="utf-8",
    )
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "initial")

    file_path.write_text(
        'import1\nimport2\nimport3\n\nclass A:\n    BINDINGS = [\n        1,\n    ]\n    CSS = """\n    App {\n    }\n',
        encoding="utf-8",
    )

    diff = get_diff(file_path)
    start = 5
    end = 7
    entries = _stage_entries(diff, start, end)
    patch = _selected_patch(
        Path("alpha.py"),
        [entry for entry in entries if start <= entry["full_index"] <= end],
    )

    assert patch is not None
    git(workspace, "apply", "--cached", "--unidiff-zero", "-", input_text=patch)

    assert git(workspace, "show", ":alpha.py") == (
        "import1\n\nclass A:\n"
        "    BINDINGS = [\n"
        "        1,\n"
        "    ]\n"
        '    CSS = """\n'
        "    App {\n"
        "    }\n"
    )


def test_review_diff_highlights_tint_the_full_line_background(monkeypatch) -> None:
    viewer = ReviewDiffView(
        [{"is_staged": False, "type": "+", "text": "added"}],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
        show_line_numbers=True,
    )
    colors = _set_theme_colors(monkeypatch)
    viewer.line_highlights = True
    base = Color.parse("#232323")
    style = _line_highlight_style(viewer, 0)

    assert style.bgcolor == base.blend(colors["success_light"], 0.25).rich_color


def test_review_diff_highlight_colors_match_status_priority(monkeypatch) -> None:
    review_view = review_view_stub()
    review_view.reviews = [
        {
            "filename": Path("alpha.py"),
            "line_number_start": 1,
            "line_number_end": 1,
        }
    ]
    viewer = ReviewDiffView(
        [
            {"is_staged": False, "type": "+", "text": "reviewed"},
            {"is_staged": False, "type": "-", "text": "removed"},
            {"is_staged": False, "type": "+", "text": "added"},
            {"is_staged": True, "type": "+", "text": "staged"},
        ],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view),
    )
    colors = _set_theme_colors(monkeypatch)
    viewer.line_highlights = True
    base = Color.parse("#232323")
    reviewed = _line_highlight_style(viewer, 0)
    removed = _line_highlight_style(viewer, 1)
    added = _line_highlight_style(viewer, 2)
    staged = _line_highlight_style(viewer, 3)

    assert reviewed.bgcolor == base.blend(colors["primary_light"], 0.25).rich_color
    assert removed.bgcolor == base.blend(colors["error_light"], 0.25).rich_color
    assert added.bgcolor == base.blend(colors["success_light"], 0.25).rich_color
    assert staged.bgcolor == base.blend(colors["secondary_light"], 0.18).rich_color


def test_review_diff_highlights_keep_using_stored_diff_line_ranges(monkeypatch) -> None:
    review_view = review_view_stub()
    review_view.reviews = [
        {
            "filename": Path("alpha.py"),
            "line_number_start": 2,
            "line_number_end": 4,
            "file_line_number_start": 2,
            "file_line_number_end": 3,
        }
    ]
    viewer = ReviewDiffView(
        [
            {"is_staged": False, "type": "", "text": "a = 1"},
            {"is_staged": False, "type": "-", "text": "b = 2"},
            {"is_staged": False, "type": "+", "text": "b = 20"},
            {"is_staged": False, "type": "", "text": "c = 3"},
            {"is_staged": False, "type": "-", "text": "e = 5"},
            {"is_staged": False, "type": "+", "text": "e = 50"},
        ],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view),
    )
    colors = _set_theme_colors(monkeypatch)
    viewer.line_highlights = True
    base = Color.parse("#232323")

    deleted = _line_highlight_style(viewer, 1)
    replacement = _line_highlight_style(viewer, 2)
    context = _line_highlight_style(viewer, 3)
    later_deleted = _line_highlight_style(viewer, 4)

    expected = base.blend(colors["primary_light"], 0.25).rich_color
    assert deleted.bgcolor == expected
    assert replacement.bgcolor == expected
    assert context.bgcolor == expected
    assert later_deleted.bgcolor == base.blend(colors["error_light"], 0.25).rich_color


def test_review_diff_gutter_width_reserves_space_for_diff_symbol() -> None:
    viewer = ReviewDiffView(
        [{"is_staged": False, "type": "", "text": str(index)} for index in range(105)],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
        show_line_numbers=True,
    )

    assert viewer.gutter_width == EXPECTED_GUTTER_WIDTH


def test_review_diff_falls_back_to_plain_text_for_missing_language() -> None:
    viewer = ReviewDiffView(
        [],
        file_path=Path("alpha.rb"),
        review_view=cast(Any, review_view_stub()),
        language="ruby",
    )

    assert viewer.language is None
    assert viewer.missing_language_package == "tree-sitter-ruby"


def test_review_diff_registers_typescript_languages() -> None:
    viewer = ReviewDiffView(
        [],
        file_path=Path("alpha.ts"),
        review_view=cast(Any, review_view_stub()),
    )

    assert "typescript" in viewer.available_languages
    assert "tsx" in viewer.available_languages


def test_review_cycle_mode_hides_deleted_lines_in_add_mode(monkeypatch) -> None:
    viewer = ReviewDiffView(
        [
            {"is_staged": False, "type": "", "text": "a = 1"},
            {"is_staged": False, "type": "-", "text": "b = 2"},
            {"is_staged": False, "type": "+", "text": "b = 20"},
            {"is_staged": False, "type": "", "text": "c = 3"},
        ],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
    )
    centers: list[bool] = []

    def move_cursor(location, *, center=False, record_width=True):
        centers.append(center)

    monkeypatch.setattr(ReviewDiffView, "is_mounted", property(lambda self: True))
    monkeypatch.setattr(viewer, "move_cursor", move_cursor)

    viewer.action_review_cycle_mode()

    assert centers[-1] is True
    assert viewer.mode == "add"
    assert viewer.border_subtitle == "add"
    assert viewer.text == "a = 1\nb = 20\nc = 3"
    assert viewer.visible_diff_lines == [0, 2, 3]

    viewer.action_review_cycle_mode()

    assert centers[-1] is True
    assert viewer.mode == "diff"
    assert viewer.border_subtitle == ""
    assert viewer.text == "a = 1\nb = 2\nb = 20\nc = 3"


def test_review_search_ignores_hidden_deleted_lines() -> None:
    review_view = review_view_stub()
    review_view.search_term = "needle"
    review_view.search_whole_word = False
    viewer = ReviewDiffView(
        [
            {"is_staged": False, "type": "", "text": "start"},
            {"is_staged": False, "type": "-", "text": "needle deleted"},
            {"is_staged": False, "type": "+", "text": "needle added"},
            {"is_staged": False, "type": "", "text": "end"},
        ],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view),
    )
    viewer.action_review_cycle_mode()

    viewer.move_cursor((0, 0), record_width=False)
    viewer.action_review_jump_next()

    assert viewer.cursor_location == (1, 0)

    viewer.move_cursor((2, 0), record_width=False)
    viewer.action_review_jump_previous()

    assert viewer.cursor_location == (1, 0)


def test_review_range_includes_line_when_text_selection_ends_at_line_start() -> None:
    viewer = ReviewDiffView(
        [{"is_staged": False, "type": "", "text": str(index)} for index in range(10)],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
    )
    viewer.selection = type(viewer.selection)((2, 0), (8, 0))

    assert _review_range(viewer) == (2, 8)


def test_review_range_excludes_next_line_for_visual_line_selection() -> None:
    viewer = ReviewDiffView(
        [{"is_staged": False, "type": "", "text": str(index)} for index in range(10)],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
    )
    viewer.line_selection_anchor = 2
    viewer.selection = type(viewer.selection)((2, 0), (8, 0))

    assert _review_range(viewer) == (2, 7)


def test_review_previous_modification_can_jump_to_first_line() -> None:
    viewer = ReviewDiffView(
        [
            {"is_staged": False, "type": "+", "text": "added"},
            {"is_staged": False, "type": "", "text": "context"},
        ],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
    )
    viewer.move_cursor((1, 0), record_width=False)

    viewer.action_review_previous_modification()

    assert viewer.cursor_location == (0, 0)


def test_next_modification_jumps_to_next_block_start() -> None:
    diff: Diff = [
        {"is_staged": False, "type": "", "text": "ctx"},
        {"is_staged": True, "type": "-", "text": "staged old"},
        {"is_staged": False, "type": "+", "text": "unstaged new"},
        {"is_staged": False, "type": "+", "text": "unstaged new 2"},
        {"is_staged": False, "type": "", "text": "ctx2"},
        {"is_staged": False, "type": "-", "text": "next old"},
    ]

    first_block_start = 2
    second_block_start = 5

    assert next_modification(diff, 0) == first_block_start
    assert next_modification(diff, first_block_start) == second_block_start
    assert next_modification(diff, first_block_start + 1) == second_block_start


def test_next_search_line_can_match_whole_words_only() -> None:
    diff: Diff = [
        {"is_staged": False, "type": "", "text": "alphabetabeta"},
        {"is_staged": False, "type": "", "text": "beta"},
    ]

    assert next_search_line(diff, "beta", -1, whole_word=True) == 1
    assert next_search_line(diff, "beta", -1, whole_word=False) == 0


def test_previous_modification_jumps_to_previous_block_start() -> None:
    diff: Diff = [
        {"is_staged": False, "type": "-", "text": "first old"},
        {"is_staged": False, "type": "+", "text": "first new"},
        {"is_staged": False, "type": "", "text": "ctx"},
        {"is_staged": True, "type": "+", "text": "staged new"},
        {"is_staged": False, "type": "-", "text": "unstaged old"},
        {"is_staged": False, "type": "+", "text": "unstaged new"},
    ]

    assert previous_modification(diff, 5) == 0
    assert previous_modification(diff, 4) == 0


def test_review_previous_cursor_position_returns_after_jump() -> None:
    viewer = ReviewDiffView(
        [
            {"is_staged": False, "type": "", "text": "alpha beta"},
            {"is_staged": False, "type": "", "text": "gamma delta"},
        ],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
    )
    viewer.move_cursor((1, 3), record_width=False)

    viewer.action_review_scroll_home()
    viewer.action_review_previous_cursor_position()

    assert viewer.cursor_location == (1, 3)


def test_jump_to_file_line_skips_deleted_lines() -> None:
    viewer = ReviewDiffView(
        [
            {"is_staged": False, "type": "", "text": "a = 1"},
            {"is_staged": False, "type": "-", "text": "b = 2"},
            {"is_staged": False, "type": "+", "text": "b = 20"},
            {"is_staged": False, "type": "", "text": "c = 3"},
            {"is_staged": False, "type": "-", "text": "e = 5"},
            {"is_staged": False, "type": "+", "text": "e = 50"},
        ],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
    )

    viewer.jump_to_file_line(4)

    assert viewer.cursor_location == (5, 0)


def test_review_next_word_and_previous_word() -> None:
    viewer = ReviewDiffView(
        [
            {"is_staged": False, "type": "", "text": "alpha beta"},
            {"is_staged": False, "type": "", "text": "gamma delta"},
        ],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
    )
    viewer.move_cursor((0, 1), record_width=False)
    viewer.action_review_next_word()

    assert viewer.cursor_location == (0, 6)

    viewer.action_review_previous_word()
    assert viewer.cursor_location == (0, 0)


def test_word_under_cursor_uses_current_word() -> None:
    viewer = ReviewDiffView(
        [
            {"is_staged": True, "type": "-", "text": 'value = "beta"'},
            {"is_staged": True, "type": "+", "text": 'value = "beta staged"'},
        ],
        file_path=Path("beta.py"),
        review_view=cast(Any, review_view_stub()),
    )
    viewer.move_cursor((1, 10), record_width=False)

    assert word_under_cursor(viewer.text.splitlines()[1], 10) == "beta"


def test_get_unstaged_files_uses_git_paths_without_loading_full_diffs(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    git(workspace, "init")
    git(workspace, "config", "user.email", "tests@example.com")
    git(workspace, "config", "user.name", "Tests")

    alpha = workspace / "alpha.py"
    beta = workspace / "beta.py"
    alpha.write_text("a = 1\n", encoding="utf-8")
    beta.write_text("b = 1\n", encoding="utf-8")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "initial")

    alpha.write_text("a = 2\n", encoding="utf-8")
    beta.write_text("b = 2\n", encoding="utf-8")
    git(workspace, "add", "beta.py")
    (workspace / "gamma.py").write_text("c = 3\n", encoding="utf-8")
    nested = workspace / "tmp-review-repro"
    nested.mkdir()
    (nested / "AGENTS.md").write_text("", encoding="utf-8")
    (nested / ".git").mkdir()
    (nested / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    assert get_unstaged_files(workspace) == [
        Path("alpha.py"),
        Path("gamma.py"),
        Path("tmp-review-repro/AGENTS.md"),
    ]


def test_stage_lines_replaces_staged_additions_changed_unstaged(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    git(workspace, "init")
    git(workspace, "config", "user.email", "tests@example.com")
    git(workspace, "config", "user.name", "Tests")

    file_path = workspace / "alpha.py"
    file_path.write_text("start = 1\n", encoding="utf-8")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "initial")

    file_path.write_text("start = 1\nshow = True\n", encoding="utf-8")
    git(workspace, "add", "alpha.py")
    file_path.write_text("start = 1\nshow = False\n", encoding="utf-8")

    diff = get_diff(file_path)
    error = apply_selected_diff_lines(
        diff,
        Path("alpha.py"),
        workspace,
        (1, 2),
        is_staged=False,
    )

    assert error is None
    assert git(workspace, "show", ":alpha.py") == "start = 1\nshow = False\n"


def test_stage_lines_works_for_untracked_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    git(workspace, "init")
    git(workspace, "config", "user.email", "tests@example.com")
    git(workspace, "config", "user.name", "Tests")

    file_path = workspace / "alpha.py"
    file_path.write_text("value = 1\nvalue = 2\n", encoding="utf-8")

    diff = get_diff(file_path)
    error = apply_selected_diff_lines(
        diff,
        Path("alpha.py"),
        workspace,
        (0, len(diff) - 1),
        is_staged=False,
    )

    assert error is None
    assert git(workspace, "show", ":alpha.py") == "value = 1\nvalue = 2\n"


async def _mounted_review_diff(diff: Diff) -> tuple[ReviewDiffApp, ReviewDiffView]:
    viewer = ReviewDiffView(
        diff,
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
        show_line_numbers=True,
        show_cursor=True,
    )

    async def _noop_reload_in_place() -> None:
        return None

    viewer.reload_in_place = cast(Any, _noop_reload_in_place)
    return ReviewDiffApp(viewer), viewer


@pytest.mark.anyio
async def test_review_page_bindings_move_by_visible_page() -> None:
    diff = [
        {"is_staged": False, "type": "", "text": str(index)} for index in range(200)
    ]
    app, viewer = await _mounted_review_diff(cast(Diff, diff))

    async with app.run_test() as pilot:
        await pilot.pause(0)

        page_height = viewer.content_size.height
        assert page_height > 0

        viewer.action_review_page_down()
        await pilot.pause(0)
        assert viewer.cursor_location == (page_height, 0)

        viewer.action_review_page_up()
        await pilot.pause(0)
        assert viewer.cursor_location == (0, 0)


@pytest.mark.anyio
async def test_review_page_bindings_clamp_at_buffer_edges() -> None:
    diff = [
        {"is_staged": False, "type": "", "text": str(index)} for index in range(200)
    ]
    app, viewer = await _mounted_review_diff(cast(Diff, diff))

    async with app.run_test() as pilot:
        await pilot.pause(0)

        viewer.move_cursor((viewer.document.line_count - 1, 0), record_width=False)
        viewer.action_review_page_down()
        await pilot.pause(0)
        assert viewer.cursor_location == (viewer.document.line_count - 1, 0)

        viewer.move_cursor((0, 0), record_width=False)
        viewer.action_review_page_up()
        await pilot.pause(0)
        assert viewer.cursor_location == (0, 0)
