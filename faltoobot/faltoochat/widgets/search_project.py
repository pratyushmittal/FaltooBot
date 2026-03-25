import json
import subprocess
from pathlib import Path
from typing import TypedDict

from .telescope import MAX_RESULTS, Telescope

PREVIEW_CHARS = 120


class ProjectSearchResult(TypedDict):
    title: str
    path: Path
    line_number: int | None
    text: str


class SearchProject(Telescope[ProjectSearchResult]):
    def __init__(self, *, workspace: Path) -> None:
        super().__init__(
            items=lambda query: _project_search_results(workspace, query),
            title="Search files and code",
            placeholder="Type a filename, path, or code",
        )


def _project_search_results(workspace: Path, query: str) -> list[ProjectSearchResult]:
    needle = query.strip()
    if not needle:
        return [
            {
                "title": str(path),
                "path": path,
                "line_number": None,
                "text": "",
            }
            for path in _rg_files(workspace)[:MAX_RESULTS]
        ]

    file_matches = _ripgrep_file_results(workspace, needle)
    grep_matches = _ripgrep_results(workspace, needle)
    grep_items: list[tuple[int, ProjectSearchResult]] = [
        (10_000 - index, result) for index, result in enumerate(grep_matches)
    ]
    matches = [*file_matches, *grep_items]
    matches.sort(key=lambda item: (-item[0], item[1]["title"]))
    return [item for _score, item in matches[:MAX_RESULTS]]


def _rg_files(workspace: Path) -> list[Path]:
    result = subprocess.run(
        ["rg", "--files"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [Path(line) for line in result.stdout.splitlines() if line]


def _result_label(path: Path, line_number: int, text: str) -> str:
    preview = text.strip()
    if len(preview) > PREVIEW_CHARS:
        preview = f"{preview[: PREVIEW_CHARS - 1]}…"
    return f"{path}:{line_number}: {preview}"


def _ripgrep_results(workspace: Path, query: str) -> list[ProjectSearchResult]:
    needle = query.strip()
    if not needle:
        return []
    result = subprocess.run(
        [
            "rg",
            "--json",
            "--line-number",
            "--color=never",
            "--smart-case",
            "--fixed-strings",
            needle,
            ".",
        ],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in {0, 1}:
        return []

    matches: list[ProjectSearchResult] = []
    for raw_line in result.stdout.splitlines():
        item = json.loads(raw_line)
        if item.get("type") != "match":
            continue
        data = item["data"]
        path = Path(data["path"]["text"])
        line_number = int(data["line_number"])
        text = data["lines"]["text"].rstrip("\n")
        matches.append(
            {
                "title": _result_label(path, line_number, text),
                "path": path,
                "line_number": line_number,
                "text": text,
            }
        )
    return matches


def _ripgrep_file_results(
    workspace: Path,
    query: str,
) -> list[tuple[int, ProjectSearchResult]]:
    files = _rg_files(workspace)
    if not files:
        return []

    result = subprocess.run(
        ["rg", "--smart-case", "--fixed-strings", query],
        input="\n".join(str(path) for path in files),
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in {0, 1}:
        return []

    return [
        (
            100_000 - index,
            {
                "title": line,
                "path": Path(line),
                "line_number": None,
                "text": "",
            },
        )
        for index, line in enumerate(result.stdout.splitlines())
        if line
    ]
