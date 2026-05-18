from pathlib import Path
from typing import Any, cast

import pytest
from textual.app import App, ComposeResult
from textual.color import Color

from faltoobot.faltoochat.diff import Diff
from faltoobot.faltoochat.review_api import reviews_prompt
from faltoobot.faltoochat.widgets.review_file import (
    ADDED_LAYOUT,
    SIDE_BY_SIDE_LAYOUT,
    UNIFIED_LAYOUT,
    ReviewFileView,
    _side_by_side_visible_diff_lines,
)
from faltoobot.faltoochat.widgets.review_diff import (
    ADDED_FILTER,
    FULL_FILTER,
    REMOVED_FILTER,
    ReviewDiffView,
    comment_title,
    _line_highlight_style,
    _review_range,
    visible_diff_lines,
)


EXPECTED_GUTTER_WIDTH = 6
EXPECTED_REVIEW_DIFF_WIDTH = 38


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
        self.layout_mode = "unified"
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
    def update_border_labels(self) -> None:
        return

    def _sibling(self, _viewer: Any) -> None:
        return None

    async def reload_in_place(self) -> None:
        return

    async def stage_visible_rows(self, *_args: Any, **_kwargs: Any) -> None:
        return


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
    ("filter_mode", "expected_lines", "expected_text", "expected_subtitle"),
    [
        (FULL_FILTER, [0, 1, 2, 3], "a = 1\nb = 2\nb = 20\nc = 3", ""),
        (ADDED_FILTER, [0, 2, 3], "a = 1\nb = 20\nc = 3", "added"),
        (REMOVED_FILTER, [0, 1, 3], "a = 1\nb = 2\nc = 3", "removed"),
    ],
)
def test_review_diff_filters_show_expected_lines(
    filter_mode: str,
    expected_lines: list[int],
    expected_text: str,
    expected_subtitle: str,
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
    assert expected_subtitle in {"", ADDED_FILTER, REMOVED_FILTER}
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


def test_review_diff_filters_insert_placeholders_to_align_side_by_side_rows() -> None:
    diff: Diff = [
        {"is_staged": False, "type": "", "text": "a = 1"},
        {"is_staged": False, "type": "+", "text": "inserted"},
        {"is_staged": False, "type": "", "text": "b = 2"},
    ]

    added = ReviewDiffView(
        diff,
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
        file_view=cast(Any, review_file_view_stub()),
        filter_mode=ADDED_FILTER,
    )
    removed = ReviewDiffView(
        diff,
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
        file_view=cast(Any, review_file_view_stub()),
        filter_mode=REMOVED_FILTER,
    )

    assert added.visible_diff_lines == [0, 1, 2]
    assert removed.visible_diff_lines == [0, None, 2]
    assert removed.text == "a = 1\n\nb = 2"


@pytest.mark.anyio
async def test_review_diff_renders_placeholder_rows_as_hatches(monkeypatch) -> None:
    viewer = ReviewDiffView(
        [
            {"is_staged": False, "type": "", "text": "a = 1"},
            {"is_staged": False, "type": "+", "text": "inserted"},
            {"is_staged": False, "type": "", "text": "b = 2"},
        ],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
        file_view=cast(Any, review_file_view_stub()),
        filter_mode=REMOVED_FILTER,
        show_line_numbers=True,
        read_only=True,
    )
    monkeypatch.setattr(ReviewDiffView, "on_focus", lambda self, event: None)

    async with ReviewDiffApp(viewer).run_test():
        strip = viewer.render_line(1)

    assert strip.text.startswith("╲")
    assert strip.text.strip("╲") == ""
    assert len(strip.text) == EXPECTED_REVIEW_DIFF_WIDTH
    assert all(segment.style and segment.style.dim for segment in strip._segments)


def test_removed_filter_keeps_staged_added_lines_for_side_by_side_base() -> None:
    diff: Diff = [
        {"is_staged": True, "type": "+", "text": "staged base"},
        {"is_staged": False, "type": "-", "text": "removed unstaged"},
        {"is_staged": False, "type": "+", "text": "added unstaged"},
    ]

    assert visible_diff_lines(diff, REMOVED_FILTER) == [0, 1]


def test_review_cycle_mode_requests_parent_layout_change(monkeypatch) -> None:
    viewer = ReviewDiffView(
        [],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
        file_view=cast(Any, review_file_view_stub()),
    )
    messages: list[Any] = []
    monkeypatch.setattr(viewer, "post_message", messages.append)

    viewer.action_review_cycle_mode()

    assert isinstance(messages[-1], ReviewDiffView.CycleLayoutRequested)


def test_review_file_cycles_unified_added_and_side_by_side_modes() -> None:
    file_view = ReviewFileView(
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
    )

    file_view.cycle_layout_mode()
    assert file_view.layout_mode == ADDED_LAYOUT
    assert file_view.left_viewer.filter_mode == ADDED_FILTER
    assert file_view.right_viewer.display is False

    file_view.cycle_layout_mode()
    assert file_view.layout_mode == SIDE_BY_SIDE_LAYOUT
    assert file_view.left_viewer.filter_mode == REMOVED_FILTER
    assert file_view.right_viewer.filter_mode == ADDED_FILTER
    assert file_view.right_viewer.display is True

    file_view.cycle_layout_mode()
    assert file_view.layout_mode == UNIFIED_LAYOUT
    assert file_view.left_viewer.filter_mode == FULL_FILTER
    assert file_view.right_viewer.display is False


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
        filter_mode=REMOVED_FILTER,
    )
    viewer.selection = type(viewer.selection)((1, 0), (3, 0))

    assert _review_range(viewer) == (1, 4)


def test_side_by_side_visible_diff_lines_do_not_pad_wrapped_context() -> None:
    diff: Diff = [
        {"is_staged": False, "type": "", "text": "context wraps a lot"},
        {"is_staged": False, "type": "-", "text": "old"},
        {"is_staged": False, "type": "+", "text": "new"},
    ]

    added, removed, added_map, removed_map = _side_by_side_visible_diff_lines(
        diff,
        added_width=5,
        removed_width=5,
        indent_width=4,
    )

    assert added == [0, 2]
    assert removed == [0, 1]
    assert added_map == [0, 2]
    assert removed_map == [0, 1]


def test_side_by_side_visible_diff_lines_pad_shorter_wrapped_side() -> None:
    diff: Diff = [
        {"is_staged": False, "type": "", "text": "start"},
        {"is_staged": False, "type": "-", "text": "old"},
        {"is_staged": False, "type": "+", "text": "new value wraps"},
        {"is_staged": False, "type": "", "text": "end"},
    ]

    added, removed, added_map, removed_map = _side_by_side_visible_diff_lines(
        diff,
        added_width=5,
        removed_width=5,
        indent_width=4,
    )

    assert added == [0, 2, 3]
    assert removed == [0, 1, None, None, None, 3]
    assert added_map == [0, 2, 3]
    assert removed_map == [0, 1, 2, 2, 2, 3]


def test_side_by_side_visible_diff_lines_map_staged_padding_to_same_line() -> None:
    diff: Diff = [
        {"is_staged": False, "type": "", "text": "start"},
        {"is_staged": True, "type": "+", "text": "staged value wraps"},
        {"is_staged": False, "type": "", "text": "end"},
    ]

    added, removed, added_map, removed_map = _side_by_side_visible_diff_lines(
        diff,
        added_width=5,
        removed_width=20,
        indent_width=4,
    )

    assert added == [0, 1, 2]
    assert removed == [0, 1, None, None, None, None, 2]
    assert added_map == [0, 1, 2]
    assert removed_map == [0, 1, 1, 1, 1, 1, 2]


@pytest.mark.anyio
async def test_review_diff_renders_cursor_on_hatched_rows(monkeypatch) -> None:
    viewer = ReviewDiffView(
        [
            {"is_staged": False, "type": "", "text": "a = 1"},
            {"is_staged": False, "type": "+", "text": "inserted"},
            {"is_staged": False, "type": "", "text": "b = 2"},
        ],
        file_path=Path("alpha.py"),
        review_view=cast(Any, review_view_stub()),
        file_view=cast(Any, review_file_view_stub()),
        filter_mode=REMOVED_FILTER,
        show_line_numbers=True,
        show_cursor=True,
        read_only=True,
    )
    monkeypatch.setattr(ReviewDiffView, "on_focus", lambda self, event: None)

    async with ReviewDiffApp(viewer).run_test():
        viewer.move_cursor((1, 0), record_width=False)
        strip = viewer.render_line(1)

    assert strip._segments[1].style == viewer._theme.cursor_style


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
