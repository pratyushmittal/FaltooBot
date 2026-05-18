from pathlib import Path
from typing import Any, cast

import pytest
from rich.segment import Segment
from rich.style import Style
from textual.app import App, ComposeResult
from textual.color import Color
from textual.strip import Strip

from faltoobot.faltoochat.diff import Diff
from faltoobot.faltoochat.review_api import reviews_prompt
from faltoobot.faltoochat.widgets.review_file import ReviewFileView
from faltoobot.faltoochat.widgets.review_diff import (
    ADDED_FILTER,
    FULL_FILTER,
    ReviewDiffView,
    comment_title,
    _apply_line_highlight,
    _line_highlight_style,
    _review_range,
    visible_diff_lines,
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
        self.line_highlights = True
        self.soft_wrap_enabled = True

    def add_review(self, _review) -> None:
        return

    async def submit_reviews(self) -> None:
        return

    def set_display_preferences(self, **_kwargs) -> None:
        return


def review_view_stub() -> ReviewViewStub:
    return ReviewViewStub()


class ReviewFileViewStub:
    pass


def review_file_view_stub() -> ReviewFileViewStub:
    return ReviewFileViewStub()


def test_reviews_prompt_renders_file_comments() -> None:
    prompt = reviews_prompt(
        [
            {
                "filename": Path("alpha.py"),
                "line_number_start": 0,
                "line_number_end": 0,
                "file_line_number_start": 0,
                "file_line_number_end": 0,
                "code": "old\n+new",
                "comment": "Consider the whole file.",
            }
        ]
    )

    assert "### File comment" in prompt
    assert "### Line `0-0`" not in prompt
    assert "Code:" not in prompt
    assert "old\n+new" not in prompt
    assert "Consider the whole file." in prompt


def test_comment_title_includes_staged_hunk_count() -> None:
    review_view = review_view_stub()
    review_view.reviews = [{"filename": Path("alpha.py")}]
    viewer = ReviewDiffView(
        [
            {"is_staged": True, "type": "-", "text": "old"},
            {"is_staged": True, "type": "+", "text": "new"},
            {"is_staged": False, "type": "", "text": "context"},
            {"is_staged": False, "type": "+", "text": "added"},
            {"is_staged": False, "type": "", "text": "context"},
            {"is_staged": True, "type": "-", "text": "removed"},
        ],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view),
        file_view=cast(Any, review_file_view_stub()),
    )

    assert comment_title(viewer) == "1 comment · 2/3 hunks staged"


def test_review_diff_highlights_tint_the_full_line_background(monkeypatch) -> None:
    viewer = ReviewDiffView(
        [{"is_staged": False, "type": "+", "text": "added"}],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
        file_view=cast(Any, review_file_view_stub()),
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
        file_view=cast(Any, review_file_view_stub()),
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


def test_review_diff_line_highlight_overlays_cursor_line_background() -> None:
    strip = Strip([Segment("added", Style(bgcolor="blue"))], 5)

    highlighted = _apply_line_highlight(
        strip,
        Style(bgcolor="green"),
        base_background=Color.from_rich_color(Style(bgcolor="blue").bgcolor),
    )

    assert highlighted._segments[0].style is not None
    assert highlighted._segments[0].style.bgcolor == Style(bgcolor="green").bgcolor


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
        file_view=cast(Any, review_file_view_stub()),
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


@pytest.mark.anyio
async def test_review_diff_render_line_draws_indent_guides(monkeypatch) -> None:
    viewer = ReviewDiffView(
        [{"is_staged": False, "type": "", "text": "        value = 1"}],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
        file_view=cast(Any, review_file_view_stub()),
        show_line_numbers=False,
        read_only=True,
    )
    monkeypatch.setattr(ReviewDiffView, "on_focus", lambda self, event: None)

    async with ReviewDiffApp(viewer).run_test():
        assert viewer.render_line(0).text.startswith("│   │   value = 1")


@pytest.mark.anyio
async def test_review_diff_indent_guides_skip_small_indents(monkeypatch) -> None:
    viewer = ReviewDiffView(
        [{"is_staged": False, "type": "", "text": "  value = 1"}],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
        file_view=cast(Any, review_file_view_stub()),
        show_line_numbers=False,
        read_only=True,
    )
    monkeypatch.setattr(ReviewDiffView, "on_focus", lambda self, event: None)

    async with ReviewDiffApp(viewer).run_test():
        assert viewer.render_line(0).text.startswith("  value = 1")


def test_review_diff_gutter_width_reserves_space_for_diff_symbol() -> None:
    viewer = ReviewDiffView(
        [{"is_staged": False, "type": "", "text": str(index)} for index in range(105)],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
        file_view=cast(Any, review_file_view_stub()),
        show_line_numbers=True,
    )

    assert viewer.gutter_width == EXPECTED_GUTTER_WIDTH


def test_review_diff_falls_back_to_plain_text_for_missing_language() -> None:
    viewer = ReviewDiffView(
        [],
        file_path=Path("alpha.rb"),
        review_view=cast(Any, review_view_stub()),
        file_view=cast(Any, review_file_view_stub()),
    )

    assert viewer.language is None
    assert viewer.missing_language_package == "tree-sitter-ruby"


def test_review_diff_registers_typescript_languages() -> None:
    viewer = ReviewDiffView(
        [],
        file_path=Path("alpha.ts"),
        review_view=cast(Any, review_view_stub()),
        file_view=cast(Any, review_file_view_stub()),
    )

    assert "typescript" in viewer.available_languages
    assert "tsx" in viewer.available_languages


@pytest.mark.parametrize(
    ("filter_mode", "expected_lines", "expected_text"),
    [
        (FULL_FILTER, [0, 1, 2, 3], "a = 1\nb = 2\nb = 20\nc = 3"),
        (ADDED_FILTER, [0, 2, 3], "a = 1\nb = 20\nc = 3"),
    ],
)
def test_review_diff_filters_show_expected_lines(
    filter_mode: str,
    expected_lines: list[int],
    expected_text: str,
) -> None:
    diff: Diff = [
        {"is_staged": False, "type": "", "text": "a = 1"},
        {"is_staged": False, "type": "-", "text": "b = 2"},
        {"is_staged": False, "type": "+", "text": "b = 20"},
        {"is_staged": False, "type": "", "text": "c = 3"},
    ]
    viewer = ReviewDiffView(
        diff,
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
        file_view=cast(Any, review_file_view_stub()),
        filter_mode=filter_mode,
    )

    assert visible_diff_lines(diff, filter_mode) == expected_lines
    assert viewer.filter_mode == filter_mode
    assert viewer.text == expected_text


def test_review_diff_hides_horizontal_scrollbar() -> None:
    viewer = ReviewDiffView(
        [{"is_staged": False, "type": "", "text": "x" * 200}],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
        file_view=cast(Any, review_file_view_stub()),
        soft_wrap=False,
    )

    assert viewer.styles.scrollbar_size_horizontal == 0


@pytest.mark.anyio
def test_review_cycle_mode_changes_the_active_diff_only() -> None:
    file_view = ReviewFileView(
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
    )

    file_view.viewer.action_review_cycle_mode()

    assert file_view.viewer.filter_mode == ADDED_FILTER
    assert file_view.viewer.border_subtitle == "alpha.py · added"
    assert file_view.right_viewer.filter_mode == FULL_FILTER
    assert file_view.right_viewer.border_subtitle == "alpha.py · unified"

    file_view.viewer.action_review_cycle_mode()

    assert file_view.viewer.filter_mode == FULL_FILTER
    assert file_view.viewer.border_subtitle == "alpha.py · unified"


def test_review_file_starts_with_split_closed() -> None:
    file_view = ReviewFileView(
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
    )

    file_view.focus_other_viewer()

    assert file_view.active_viewer is file_view.viewer
    assert file_view.right_viewer.display is False


@pytest.mark.anyio
async def test_review_file_open_split_shows_current_file_in_right_pane() -> None:
    file_view = ReviewFileView(
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
    )
    file_view.viewer.set_diff(
        [
            {"is_staged": False, "type": "", "text": "context"},
            {"is_staged": False, "type": "+", "text": "added"},
        ]
    )

    await file_view.open_split()

    assert file_view.active_viewer is file_view.right_viewer
    assert file_view.right_viewer.display is True
    assert file_view.right_viewer.file_path == Path("alpha.py")
    assert file_view.right_viewer.text == file_view.viewer.text


@pytest.mark.anyio
async def test_review_file_open_split_recenters_left_pane_after_layout(
    monkeypatch,
) -> None:
    file_view = ReviewFileView(
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
    )
    file_view.viewer.set_diff(
        [
            {"is_staged": False, "type": "", "text": "context"},
            {"is_staged": False, "type": "+", "text": "added"},
        ]
    )
    file_view.viewer.move_cursor((1, 0), record_width=False)
    calls = []
    monkeypatch.setattr(file_view, "call_after_refresh", lambda callback: callback())
    monkeypatch.setattr(
        file_view.viewer,
        "show_diff_line",
        lambda diff_line, *, center=False: calls.append((diff_line, center)),
    )

    await file_view.open_split()

    assert calls == [(1, True)]


def test_review_file_close_split_recenters_left_pane_after_layout(monkeypatch) -> None:
    file_view = ReviewFileView(
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
    )
    file_view.viewer.set_diff(
        [
            {"is_staged": False, "type": "", "text": "context"},
            {"is_staged": False, "type": "+", "text": "added"},
        ]
    )
    file_view.viewer.move_cursor((1, 0), record_width=False)
    file_view.right_viewer.display = True
    file_view.active_viewer = file_view.right_viewer
    calls = []
    monkeypatch.setattr(file_view, "call_after_refresh", lambda callback: callback())
    monkeypatch.setattr(
        file_view.viewer,
        "show_diff_line",
        lambda diff_line, *, center=False: calls.append((diff_line, center)),
    )

    file_view.close_split()

    assert calls == [(1, True)]


@pytest.mark.anyio
async def test_review_file_focus_other_viewer_only_switches_focus(monkeypatch) -> None:
    file_view = ReviewFileView(
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
    )
    file_view.viewer.set_diff(
        [
            {"is_staged": False, "type": "", "text": "context"},
            {"is_staged": False, "type": "+", "text": "added"},
        ]
    )
    await file_view.open_split()
    monkeypatch.setattr(file_view.viewer, "refresh", pytest.fail)
    monkeypatch.setattr(file_view.right_viewer, "refresh", pytest.fail)
    monkeypatch.setattr(file_view.viewer, "current_diff_line", pytest.fail)
    monkeypatch.setattr(file_view.right_viewer, "show_diff_line", pytest.fail)

    file_view.focus_other_viewer()

    assert file_view.active_viewer is file_view.viewer

    file_view.focus_other_viewer()
    assert file_view.active_viewer is file_view.right_viewer


def test_review_range_maps_filtered_rows_back_to_backing_diff_lines() -> None:
    viewer = ReviewDiffView(
        [
            {"is_staged": False, "type": "", "text": "a = 1"},
            {"is_staged": False, "type": "-", "text": "b = 2"},
            {"is_staged": False, "type": "+", "text": "b = 20"},
            {"is_staged": False, "type": "", "text": "c = 3"},
            {"is_staged": False, "type": "-", "text": "d = 4"},
        ],
        file_path=Path("app/alpha.py"),
        review_view=cast(Any, review_view_stub()),
        file_view=cast(Any, review_file_view_stub()),
        filter_mode=ADDED_FILTER,
    )
    viewer.selection = type(viewer.selection)((1, 0), (3, 0))

    assert _review_range(viewer) == (2, 3)


@pytest.mark.anyio
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
        file_view=cast(Any, review_file_view_stub()),
    )
    viewer.set_filter_mode(ADDED_FILTER, force=True)

    viewer.move_cursor((0, 0), record_width=False)
    viewer.action_review_jump_next()

    assert viewer.cursor_location == (1, 0)

    viewer.move_cursor((2, 0), record_width=False)
    viewer.action_review_jump_previous()

    assert viewer.cursor_location == (1, 0)


def test_review_range_uses_cursor_for_empty_selection() -> None:
    viewer = ReviewDiffView(
        [{"is_staged": False, "type": "", "text": str(index)} for index in range(5)],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
        file_view=cast(Any, review_file_view_stub()),
    )
    viewer.selection = type(viewer.selection)((0, 0), (0, 0))
    viewer.move_cursor((3, 0), record_width=False)

    assert _review_range(viewer) == (3, 3)


def test_review_range_includes_line_when_text_selection_ends_at_line_start() -> None:
    viewer = ReviewDiffView(
        [{"is_staged": False, "type": "", "text": str(index)} for index in range(10)],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
        file_view=cast(Any, review_file_view_stub()),
    )
    viewer.selection = type(viewer.selection)((2, 0), (8, 0))

    assert _review_range(viewer) == (2, 8)


def test_review_range_uses_visual_line_selection_rows() -> None:
    viewer = ReviewDiffView(
        [{"is_staged": False, "type": "", "text": str(index)} for index in range(10)],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
        file_view=cast(Any, review_file_view_stub()),
    )
    viewer.line_selection_anchor = 2
    viewer.line_selection_cursor = 7
    viewer.selection = type(viewer.selection)((2, 0), (7, 1))

    assert _review_range(viewer) == (2, 7)


def test_review_previous_cursor_position_returns_after_jump() -> None:
    viewer = ReviewDiffView(
        [
            {"is_staged": False, "type": "", "text": "alpha beta"},
            {"is_staged": False, "type": "", "text": "gamma delta"},
        ],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
        file_view=cast(Any, review_file_view_stub()),
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
        file_view=cast(Any, review_file_view_stub()),
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
        file_view=cast(Any, review_file_view_stub()),
    )
    viewer.move_cursor((0, 1), record_width=False)
    viewer.action_review_next_word()

    assert viewer.cursor_location == (0, 6)

    viewer.action_review_previous_word()
    assert viewer.cursor_location == (0, 0)


async def _mounted_review_diff(diff: Diff) -> tuple[ReviewDiffApp, ReviewDiffView]:
    viewer = ReviewDiffView(
        diff,
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
        file_view=cast(Any, review_file_view_stub()),
        show_line_numbers=True,
        show_cursor=True,
    )

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
