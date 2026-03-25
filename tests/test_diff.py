import subprocess
from pathlib import Path

from faltoobot.faltoochat.diff import get_diff


def git(workspace: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_get_diff_returns_staged_and_unstaged_lines(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    git(workspace, "init")
    git(workspace, "config", "user.email", "tests@example.com")
    git(workspace, "config", "user.name", "Tests")

    file_path = workspace / "alpha.py"
    file_path.write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "initial")

    file_path.write_text("a = 1\nb = 20\nc = 3\n", encoding="utf-8")
    git(workspace, "add", "alpha.py")
    file_path.write_text("a = 1\nb = 20\nc = 30\n", encoding="utf-8")

    diff = get_diff(file_path)

    assert diff == [
        {"is_staged": False, "type": "", "text": "a = 1"},
        {"is_staged": True, "type": "-", "text": "b = 2"},
        {"is_staged": True, "type": "+", "text": "b = 20"},
        {"is_staged": False, "type": "-", "text": "c = 3"},
        {"is_staged": False, "type": "+", "text": "c = 30"},
    ]


def test_get_diff_returns_untracked_file_lines(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    git(workspace, "init")
    git(workspace, "config", "user.email", "tests@example.com")
    git(workspace, "config", "user.name", "Tests")

    file_path = workspace / "beta.py"
    file_path.write_text("value = 1\nvalue = 2\n", encoding="utf-8")

    diff = get_diff(file_path)

    assert diff == [
        {"is_staged": False, "type": "+", "text": "value = 1"},
        {"is_staged": False, "type": "+", "text": "value = 2"},
    ]


def test_get_diff_does_not_duplicate_context_for_mixed_stage_states(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    git(workspace, "init")
    git(workspace, "config", "user.email", "tests@example.com")
    git(workspace, "config", "user.name", "Tests")

    file_path = workspace / "alpha.py"
    file_path.write_text("a = 1\nb = 2\nc = 3\nd = 4\n", encoding="utf-8")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "initial")

    file_path.write_text("a = 1\nb = 20\nc = 3\nd = 40\n", encoding="utf-8")
    git(workspace, "add", "alpha.py")
    file_path.write_text("a = 1\nb = 20\nc = 30\nd = 40\n", encoding="utf-8")

    diff = get_diff(file_path)

    assert diff == [
        {"is_staged": False, "type": "", "text": "a = 1"},
        {"is_staged": True, "type": "-", "text": "b = 2"},
        {"is_staged": True, "type": "+", "text": "b = 20"},
        {"is_staged": False, "type": "-", "text": "c = 3"},
        {"is_staged": True, "type": "-", "text": "d = 4"},
        {"is_staged": False, "type": "+", "text": "c = 30"},
        {"is_staged": True, "type": "+", "text": "d = 40"},
    ]
    assert sum(1 for line in diff if line["text"] == "a = 1") == 1


def test_get_diff_does_not_triple_show_staged_additions_changed_unstaged(
    tmp_path: Path,
) -> None:
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

    assert diff == [
        {"is_staged": False, "type": "", "text": "start = 1"},
        {"is_staged": False, "type": "-", "text": "show = True"},
        {"is_staged": False, "type": "+", "text": "show = False"},
    ]
