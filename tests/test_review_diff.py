import subprocess
from pathlib import Path

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
from faltoobot.faltoochat.widgets.review_diff import ReviewDiffView, _review_range


EXPECTED_GUTTER_WIDTH = 6


class ReviewViewStub:
    def __init__(self) -> None:
        self.active_pane = None
        self.reviews = []

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


def test_review_diff_gutter_width_reserves_space_for_diff_symbol() -> None:
    viewer = ReviewDiffView(
        [{"is_staged": False, "type": "", "text": str(index)} for index in range(105)],
        file_path=Path("alpha.py"),
        review_view=review_view_stub(),  # type: ignore[arg-type]
        show_line_numbers=True,
    )

    assert viewer.gutter_width == EXPECTED_GUTTER_WIDTH


def test_review_range_uses_selected_text_to_include_last_selected_line() -> None:
    viewer = ReviewDiffView(
        [{"is_staged": False, "type": "", "text": str(index)} for index in range(10)],
        file_path=Path("alpha.py"),
        review_view=review_view_stub(),  # type: ignore[arg-type]
    )
    viewer.selection = type(viewer.selection)((2, 0), (8, 0))

    assert _review_range(viewer) == (2, 7)


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
        review_view=review_view_stub(),  # type: ignore[arg-type]
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
        review_view=review_view_stub(),  # type: ignore[arg-type]
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
        review_view=review_view_stub(),  # type: ignore[arg-type]
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

    assert get_unstaged_files(workspace) == [Path("alpha.py"), Path("gamma.py")]


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
