import subprocess
from pathlib import Path

from faltoobot.faltoochat.diff import get_diff
from faltoobot.faltoochat.git import (
    _selected_patch,
    _stage_entries,
    apply_selected_diff_lines,
    get_unstaged_files,
)


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


def init_repo(workspace: Path) -> None:
    workspace.mkdir()
    git(workspace, "init")
    git(workspace, "config", "user.email", "tests@example.com")
    git(workspace, "config", "user.name", "Tests")


def test_selected_patch_stages_insertions_at_the_right_location(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    init_repo(workspace)

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


def test_get_unstaged_files_uses_git_paths_without_loading_full_diffs(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    init_repo(workspace)

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
    init_repo(workspace)

    file_path = workspace / "alpha.py"
    file_path.write_text("start = 1\n", encoding="utf-8")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "initial")

    file_path.write_text("start = 1\nshow = True\n", encoding="utf-8")
    git(workspace, "add", "alpha.py")
    file_path.write_text("start = 1\nshow = False\n", encoding="utf-8")

    error = apply_selected_diff_lines(
        get_diff(file_path),
        Path("alpha.py"),
        workspace,
        {1, 2},
        is_staged=False,
    )

    assert error is None
    assert git(workspace, "show", ":alpha.py") == "start = 1\nshow = False\n"


def test_stage_lines_works_for_untracked_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    init_repo(workspace)

    file_path = workspace / "alpha.py"
    file_path.write_text("value = 1\nvalue = 2\n", encoding="utf-8")
    diff = get_diff(file_path)
    error = apply_selected_diff_lines(
        diff,
        Path("alpha.py"),
        workspace,
        set(range(len(diff))),
        is_staged=False,
    )

    assert error is None
    assert git(workspace, "show", ":alpha.py") == "value = 1\nvalue = 2\n"
