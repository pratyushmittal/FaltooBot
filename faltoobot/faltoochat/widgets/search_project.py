import json
import shutil
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
        self.workspace = workspace
        self._files: list[Path] | None = None
        super().__init__(
            items=self._search_results,
            title="Search files and code",
            placeholder="Type a filename, path, or code",
        )

    def on_mount(self) -> None:
        super().on_mount()
        if _has_ripgrep():
            return
        self.app.notify(
            "Install ripgrep (`rg`) to search project files.",
            severity="warning",
        )

    def _search_results(self, query: str) -> list[ProjectSearchResult]:
        return _project_search_results(
            self.workspace,
            query,
            files=self._cached_files(),
        )

    def _cached_files(self) -> list[Path]:
        if self._files is None:
            self._files = _project_files(self.workspace)
        return self._files


def _project_search_results(
    workspace: Path,
    query: str,
    *,
    files: list[Path] | None = None,
) -> list[ProjectSearchResult]:
    needle = query.strip()
    files = _project_files(workspace) if files is None else files
    if not needle:
        return [
            {
                "title": str(path),
                "path": path,
                "line_number": None,
                "text": "",
            }
            for path in files[:MAX_RESULTS]
        ]

    file_matches = _ripgrep_file_results(workspace, needle, files)
    grep_matches = _ripgrep_results(workspace, needle)
    grep_items: list[tuple[int, ProjectSearchResult]] = [
        (10_000 - index, result) for index, result in enumerate(grep_matches)
    ]
    matches = [*file_matches, *grep_items]
    matches.sort(key=lambda item: (-item[0], item[1]["title"]))
    return [item for _score, item in matches[:MAX_RESULTS]]


def _project_files(workspace: Path) -> list[Path]:
    result = _run_rg(["rg", "--files"], workspace)
    if result is None or result.returncode != 0:
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
    if result is None or result.returncode not in {0, 1}:
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
    files: list[Path],
) -> list[tuple[int, ProjectSearchResult]]:
    if not files:
        return []

    result = _run_rg(
        ["rg", "--smart-case", "--fixed-strings", query],
        workspace,
        input="\n".join(str(path) for path in files),
    )
    if result is None or result.returncode not in {0, 1}:
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


def _has_ripgrep() -> bool:
    return shutil.which("rg") is not None
