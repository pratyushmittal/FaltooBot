import subprocess
from pathlib import Path
from typing import Any

from .diff import Diff


def stage_file(workspace: Path, file_path: Path) -> str | None:
    """Stage the full file in git."""
    result = subprocess.run(
        ["git", "add", "--", str(file_path)],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return None
    return (result.stderr or result.stdout or "Could not stage the file.").strip()


def apply_selected_diff_lines(
    diff: Diff,
    file_path: Path,
    workspace: Path,
    selection: tuple[int, int],
    *,
    is_staged: bool,
) -> str | None:
    """Apply the selected diff lines to the git index by staging or unstaging them."""
    if not diff:
        return "No diff available."
    start, end = selection
    entries = _unstage_entries(diff) if is_staged else _stage_entries(diff, start, end)
    selected_entries = [
        entry
        for entry in entries
        if start <= entry["full_index"] <= end
        and entry["line"]["type"] in {"+", "-"}
        and entry["line"]["is_staged"] == is_staged
    ]
    if not selected_entries:
        return "No modified lines to stage or unstage here."
    if not is_staged:
        _ensure_index_entry(workspace, file_path)
    patch = _selected_patch(file_path, selected_entries)
    if patch is None:
        return "No modified lines to stage or unstage here."
    result = subprocess.run(
        [
            "git",
            "apply",
            "--cached",
            *(["--reverse"] if is_staged else []),
            "--unidiff-zero",
            "-",
        ],
        cwd=workspace,
        input=patch,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return None
    return (
        result.stderr or result.stdout or "Could not stage the selected lines."
    ).strip()


def get_selected_change_state(
    diff: Diff,
    cursor_line: int,
    start: int,
    end: int,
) -> bool | None:
    """Return whether the current selection targets staged lines, unstaged lines, or no change."""
    selected = [
        line["is_staged"]
        for line in diff[start : end + 1]
        if line["type"] in {"+", "-"}
    ]
    if selected:
        return selected[0]
    if diff[cursor_line]["type"] in {"+", "-"}:
        return diff[cursor_line]["is_staged"]
    return None


def is_git_workspace(workspace: Path) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _git_paths(workspace: Path, *args: str) -> list[Path]:
    result = subprocess.run(
        ["git", *args],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in {0, 1}:
        return []
    return [Path(path) for path in result.stdout.split("\0") if path]


def get_unstaged_files(workspace: Path) -> list[Path]:
    """Return modified or untracked file paths that still have unstaged changes."""
    tracked_paths = _git_paths(workspace, "diff", "--name-only", "-z")
    untracked_paths = _git_paths(
        workspace,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    )
    files: list[Path] = []
    seen: set[Path] = set()
    for path in [*tracked_paths, *untracked_paths]:
        if path in seen:
            continue
        seen.add(path)
        files.append(path)
    return files


def _ensure_index_entry(workspace: Path, file_path: Path) -> None:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(file_path)],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return
    subprocess.run(
        ["git", "add", "--intent-to-add", str(file_path)],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )


def _stage_entries(diff: Diff, start: int, end: int) -> list[dict[str, Any]]:
    selected = {
        index
        for index in range(start, end + 1)
        if diff[index]["type"] in {"+", "-"} and not diff[index]["is_staged"]
    }
    entries: list[dict[str, Any]] = []
    old_line = 1
    new_line = 1
    for full_index, line in enumerate(diff):
        if line["type"] == "":
            old_line += 1
            new_line += 1
            continue
        if line["is_staged"]:
            if line["type"] == "+":
                old_line += 1
                new_line += 1
            continue
        entries.append(
            {
                "full_index": full_index,
                "line": line,
                "old_line": old_line,
                "new_line": new_line,
            }
        )
        if full_index in selected:
            if line["type"] == "-":
                old_line += 1
            else:
                new_line += 1
            continue
        # comment: unselected unstaged deletions stay in the partial patch as context.
        if line["type"] == "-":
            old_line += 1
            new_line += 1
    return entries


def _unstage_entries(diff: Diff) -> list[dict[str, Any]]:
    return _diff_entries(diff)


def _diff_entries(diff: Diff) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    old_line = 1
    new_line = 1
    for full_index, line in enumerate(diff):
        entries.append(
            {
                "full_index": full_index,
                "line": line,
                "old_line": old_line,
                "new_line": new_line,
            }
        )
        if line["type"] == "":
            old_line += 1
            new_line += 1
        elif line["type"] == "-":
            old_line += 1
        else:
            new_line += 1
    return entries


def _selected_patch(file_path: Path, entries: list[dict[str, Any]]) -> str | None:
    groups: list[list[dict[str, Any]]] = []
    current_group: list[dict[str, Any]] = []
    for entry in entries:
        if not current_group:
            current_group = [entry]
            continue
        previous = current_group[-1]
        if entry["full_index"] == previous["full_index"] + 1:
            current_group.append(entry)
            continue
        groups.append(current_group)
        current_group = [entry]
    if current_group:
        groups.append(current_group)
    if not groups:
        return None

    hunks: list[str] = []
    for group in groups:
        first = group[0]
        old_start = int(first["old_line"])
        new_start = int(first["new_line"])
        old_count = sum(1 for entry in group if entry["line"]["type"] == "-")
        new_count = sum(1 for entry in group if entry["line"]["type"] == "+")
        if old_count == 0:
            # comment: unified zero-context insert hunks anchor on the previous old line.
            old_start = max(0, old_start - 1)
        if new_count == 0:
            # comment: unified zero-context delete hunks anchor on the previous new line.
            new_start = max(0, new_start - 1)
        hunk_lines = [
            f"{entry['line']['type']}{entry['line']['text']}" for entry in group
        ]
        hunks.extend(
            [
                f"@@ -{old_start},{old_count} +{new_start},{new_count} @@",
                *hunk_lines,
            ]
        )

    path_text = str(file_path)
    return "\n".join(
        [
            f"diff --git a/{path_text} b/{path_text}",
            f"--- a/{path_text}",
            f"+++ b/{path_text}",
            *hunks,
            "",
        ]
    )
