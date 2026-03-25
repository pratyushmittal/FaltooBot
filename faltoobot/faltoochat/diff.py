import subprocess
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal, TypeAlias, TypedDict


class Line(TypedDict):
    is_staged: bool
    type: Literal["+", "-", ""]
    text: str


Diff: TypeAlias = list[Line]


def _git_text(cwd: Path, *args: str) -> str | None:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in {0, 1}:
        return None
    return result.stdout


def _repo_root(filepath: Path) -> Path | None:
    probe = filepath.parent if filepath.is_file() or filepath.suffix else filepath
    root = _git_text(probe, "rev-parse", "--show-toplevel")
    if root is None:
        return None
    return Path(root.strip())


def _is_tracked(repo_root: Path, relative_path: Path) -> bool:
    return bool(_git_text(repo_root, "ls-files", "--error-unmatch", str(relative_path)))


def _modified_paths(repo_root: Path) -> list[Path]:
    output = _git_text(
        repo_root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    if output is None:
        return []

    paths: list[Path] = []
    parts = output.split("\0")
    index = 0
    while index < len(parts):
        entry = parts[index]
        # comment: porcelain status ends with an empty trailing item after the final NUL.
        if not entry:
            break
        status = entry[:2]
        path_text = entry[3:]
        if "R" in status or "C" in status:
            index += 1
            # comment: renamed and copied entries carry the destination path in the next slot.
            if index < len(parts) and parts[index]:
                path_text = parts[index]
        paths.append(Path(path_text))
        index += 1
    return paths


def _git_show_lines(repo_root: Path, spec: str) -> list[str]:
    text = _git_text(repo_root, "show", spec)
    if text is None:
        return []
    return text.splitlines()


def _worktree_lines(filepath: Path) -> list[str]:
    if not filepath.exists():
        return []
    return filepath.read_text(encoding="utf-8", errors="replace").splitlines()


def _staged_stream(base_lines: list[str], index_lines: list[str]) -> Diff:
    stream: Diff = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(
        a=base_lines, b=index_lines
    ).get_opcodes():
        if tag == "equal":
            stream.extend(
                [
                    {"is_staged": False, "type": "", "text": text}
                    for text in base_lines[i1:i2]
                ]
            )
            continue
        if tag in {"delete", "replace"}:
            stream.extend(
                [
                    {"is_staged": True, "type": "-", "text": text}
                    for text in base_lines[i1:i2]
                ]
            )
        if tag in {"insert", "replace"}:
            stream.extend(
                [
                    {"is_staged": True, "type": "+", "text": text}
                    for text in index_lines[j1:j2]
                ]
            )
    return stream


def _split_stream(stream: Diff) -> tuple[list[list[Line]], Diff]:
    gaps: list[list[Line]] = [[]]
    present: Diff = []
    for line in stream:
        if line["type"] == "-":
            gaps[-1].append(line)
            continue
        present.append(line)
        gaps.append([])
    return gaps, present


def _combined_stream(staged_stream: Diff, work_lines: list[str]) -> Diff:
    gaps, present = _split_stream(staged_stream)
    present_text = [line["text"] for line in present]
    stream: Diff = [*gaps[0]]
    for tag, i1, i2, j1, j2 in SequenceMatcher(
        a=present_text, b=work_lines
    ).get_opcodes():
        if tag == "equal":
            for index in range(i1, i2):
                stream.append(present[index])
                stream.extend(gaps[index + 1])
            continue
        if tag in {"delete", "replace"}:
            for index in range(i1, i2):
                line = present[index]
                stream.append({"is_staged": False, "type": "-", "text": line["text"]})
                stream.extend(gaps[index + 1])
        if tag in {"insert", "replace"}:
            stream.extend(
                [
                    {"is_staged": False, "type": "+", "text": text}
                    for text in work_lines[j1:j2]
                ]
            )
    return stream


def get_diff(filepath: Path) -> Diff:
    filepath = filepath.expanduser().resolve()
    repo_root = _repo_root(filepath)
    if repo_root is None:
        return []
    relative_path = filepath.relative_to(repo_root)
    base_lines = _git_show_lines(repo_root, f"HEAD:{relative_path}")
    index_lines = _git_show_lines(repo_root, f":{relative_path}")
    # comment: untracked files don't exist in HEAD or the index, so show the worktree as pure additions.
    if not base_lines and not index_lines and not _is_tracked(repo_root, relative_path):
        return [
            {"is_staged": False, "type": "+", "text": text}
            for text in _worktree_lines(filepath)
        ]
    return _combined_stream(
        _staged_stream(base_lines, index_lines), _worktree_lines(filepath)
    )
