import json
import shutil
import subprocess
from pathlib import Path
from typing import TypedDict

from .telescope import MAX_RESULTS, Telescope, _fuzzy_score

PREVIEW_CHARS = 120
OPEN_FILE_SYMBOL = "·"


class ProjectSearchResult(TypedDict):
    title: str
    path: Path
    line_number: int | None
    text: str


class SearchProject(Telescope[ProjectSearchResult]):
    def __init__(
        self,
        *,
        workspace: Path,
        preferred_files: list[Path] | None = None,
    ) -> None:
        self.workspace = workspace
        self._files: list[Path] | None = None
        self.preferred_files = set(preferred_files or [])
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
            preferred_files=self.preferred_files,
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
    preferred_files: set[Path] | None = None,
) -> list[ProjectSearchResult]:
    needle = query.strip()
    files = _project_files(workspace) if files is None else files
    preferred_files = preferred_files or set()

    if not needle:
        # comment: show files immediately before the user starts typing.
        preferred = [path for path in files if path in preferred_files]
        ordered = preferred + [path for path in files if path not in preferred_files]
        return [
            {
                "title": f"{path} {OPEN_FILE_SYMBOL}"
                if path in preferred_files
                else str(path),
                "path": path,
                "line_number": None,
                "text": "",
            }
            for path in ordered[:MAX_RESULTS]
        ]

    # comment: file paths are fuzzy-matched; code search remains exact grep.
    file_matches = _file_results(needle, files, preferred_files=preferred_files)
    grep_matches = _ripgrep_results(workspace, needle)

    grep_items: list[tuple[int, ProjectSearchResult]] = [
        (
            10_000 - index,
            (
                {**result, "title": f"{result['title']} {OPEN_FILE_SYMBOL}"}
                if result["path"] in preferred_files
                else result
            ),
        )
        for index, result in enumerate(grep_matches)
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
    process = _start_rg(
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
    if process is None or process.stdout is None:
        return []

    matches: list[ProjectSearchResult] = []
    try:
        for raw_line in process.stdout:
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
            # comment: broad searches can return massive output, so stop after the UI limit.
            if len(matches) >= MAX_RESULTS:
                process.kill()
                break
    finally:
        process.wait()
    if process.returncode not in {0, 1} and len(matches) < MAX_RESULTS:
        return []
    return matches


def _file_results(
    query: str,
    files: list[Path],
    *,
    preferred_files: set[Path] | None = None,
) -> list[tuple[int, ProjectSearchResult]]:
    needle = query.strip().lower()
    if not needle or not files:
        return []

    preferred_files = preferred_files or set()
    matches: list[tuple[int, ProjectSearchResult]] = []
    for path in files:
        score = _fuzzy_score(needle, str(path))
        if score is None:
            continue
        matches.append(
            (
                (1_000_000 if path in preferred_files else 100_000) + score,
                {
                    "title": f"{path} {OPEN_FILE_SYMBOL}"
                    if path in preferred_files
                    else str(path),
                    "path": path,
                    "line_number": None,
                    "text": "",
                },
            )
        )
    matches.sort(key=lambda item: (-item[0], item[1]["title"]))
    return matches[:MAX_RESULTS]


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


def _start_rg(
    args: list[str],
    workspace: Path,
    *,
    input: str | None = None,
) -> subprocess.Popen[str] | None:
    try:
        process = subprocess.Popen(
            args,
            cwd=workspace,
            stdin=subprocess.PIPE if input is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        return None
    if input is not None and process.stdin is not None:
        process.stdin.write(input)
        process.stdin.close()
    return process


def _has_ripgrep() -> bool:
    return shutil.which("rg") is not None
