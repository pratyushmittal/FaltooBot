import json
import subprocess
from pathlib import Path
from typing import TypedDict, cast

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
            for path in _project_files(workspace)[:MAX_RESULTS]
        ]

    file_matches = _ripgrep_file_results(workspace, needle)
    grep_matches = _ripgrep_results(workspace, needle)
    grep_items: list[tuple[int, ProjectSearchResult]] = [
        (10_000 - index, result) for index, result in enumerate(grep_matches)
    ]
    matches = [*file_matches, *grep_items]
    matches.sort(key=lambda item: (-item[0], item[1]["title"]))
    return [item for _score, item in matches[:MAX_RESULTS]]


def _project_files(workspace: Path) -> list[Path]:
    result = _run_rg(["rg", "--files"], workspace)
    if result is not None and result.returncode == 0:
        return [Path(line) for line in result.stdout.splitlines() if line]
    return cast(
        list[Path],
        sorted(
            [
                Path(str(path.relative_to(workspace)))
                for path in workspace.rglob("*")
                if path.is_file() and ".git" not in path.parts
            ],
            key=str,
        ),
    )


def _result_label(path: Path, line_number: int, text: str) -> str:
    preview = text.strip()
    if len(preview) > PREVIEW_CHARS:
        preview = f"{preview[: PREVIEW_CHARS - 1]}…"
    return f"{path}:{line_number}: {preview}"


def _ripgrep_results(workspace: Path, query: str) -> list[ProjectSearchResult]:
    needle = query.strip()
    if not needle:
        return []
    result = _run_rg(
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
        workspace,
    )
    if result is None:
        return _fallback_grep_results(workspace, needle)
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
    files = _project_files(workspace)
    if not files:
        return []

    result = _run_rg(
        ["rg", "--smart-case", "--fixed-strings", query],
        workspace,
        input="\n".join(str(path) for path in files),
    )
    if result is None:
        return _fallback_file_results(files, query)
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


def _run_rg(
    args: list[str],
    workspace: Path,
    *,
    input: str | None = None,
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            args,
            input=input,
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None


def _fallback_file_results(
    files: list[Path],
    query: str,
) -> list[tuple[int, ProjectSearchResult]]:
    return [
        (
            100_000 - index,
            {
                "title": str(path),
                "path": path,
                "line_number": None,
                "text": "",
            },
        )
        for index, path in enumerate(files)
        if _matches_query(str(path), query)
    ]


def _fallback_grep_results(workspace: Path, query: str) -> list[ProjectSearchResult]:
    matches: list[ProjectSearchResult] = []
    for path in _project_files(workspace):
        text = _read_text(workspace / path)
        if text is None:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not _matches_query(line, query):
                continue
            matches.append(
                {
                    "title": _result_label(path, line_number, line),
                    "path": path,
                    "line_number": line_number,
                    "text": line,
                }
            )
            if len(matches) >= MAX_RESULTS:
                return matches
    return matches


def _matches_query(text: str, query: str) -> bool:
    if any(char.isupper() for char in query):
        return query in text
    return query.lower() in text.lower()


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
