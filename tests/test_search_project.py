import json
from pathlib import Path

import pytest

from faltoobot.faltoochat.widgets.search_project import (
    SearchProject,
    _file_results,
    _project_search_results,
    _ripgrep_results,
)
from faltoobot.faltoochat.widgets.telescope import MAX_RESULTS


def test_project_search_stops_after_max_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProcess:
        def __init__(self) -> None:
            self.returncode = 0
            self.killed = False
            self.stdout = iter(
                json.dumps(
                    {
                        "type": "match",
                        "data": {
                            "path": {"text": "alpha.py"},
                            "line_number": index + 1,
                            "lines": {"text": f"line {index}\n"},
                        },
                    }
                )
                for index in range(MAX_RESULTS * 2)
            )

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        def wait(self) -> int:
            return self.returncode

    process = FakeProcess()
    monkeypatch.setattr(
        "faltoobot.faltoochat.widgets.search_project._start_rg",
        lambda *_args, **_kwargs: process,
    )

    assert len(_ripgrep_results(Path("."), "f")) == MAX_RESULTS
    assert process.killed is True


def test_search_project_caches_project_files(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Path] = []

    monkeypatch.setattr(
        "faltoobot.faltoochat.widgets.search_project._project_files",
        lambda workspace: calls.append(workspace) or [Path("alpha.py")],
    )
    monkeypatch.setattr(
        "faltoobot.faltoochat.widgets.search_project._file_results",
        lambda _query, _files, **_kwargs: [],
    )
    monkeypatch.setattr(
        "faltoobot.faltoochat.widgets.search_project._ripgrep_results",
        lambda _workspace, _query: [],
    )

    search = SearchProject(workspace=Path("."))

    assert search._search_results("") == [
        {"title": "alpha.py", "path": Path("alpha.py"), "line_number": None, "text": ""}
    ]
    assert search._search_results("alpha") == []
    assert calls == [Path(".")]


def test_search_project_fuzzy_matches_file_paths() -> None:
    matches = _file_results(
        "announcementmode",
        [Path("announcements/models.py"), Path("announcements/filters.py")],
    )

    assert [item["path"] for _score, item in matches] == [
        Path("announcements/models.py")
    ]


def test_search_project_prioritizes_preferred_file_matches() -> None:
    matches = _file_results(
        "views.py",
        [
            Path("alpha/views.py"),
            Path("changed/views.py"),
            Path("zeta/views.py"),
        ],
        preferred_files={Path("changed/views.py")},
    )

    assert matches[0][1]["path"] == Path("changed/views.py")
    assert matches[0][1]["title"] == "changed/views.py ·"


def test_project_search_shows_preferred_files_first_for_empty_query() -> None:
    results = _project_search_results(
        Path("."),
        "",
        files=[Path("alpha.py"), Path("changed.py"), Path("beta.py")],
        preferred_files={Path("changed.py")},
    )

    assert [(item["path"], item["title"]) for item in results] == [
        (Path("changed.py"), "changed.py ·"),
        (Path("alpha.py"), "alpha.py"),
        (Path("beta.py"), "beta.py"),
    ]


def test_project_search_returns_empty_without_rg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "alpha.py").write_text('value = "alpha"\n', encoding="utf-8")
    (workspace / "beta.py").write_text("answer = 50\n", encoding="utf-8")

    def missing_rg(*_args, **_kwargs):
        raise FileNotFoundError("rg")

    monkeypatch.setattr(
        "faltoobot.faltoochat.widgets.search_project.subprocess.run", missing_rg
    )
    monkeypatch.setattr(
        "faltoobot.faltoochat.widgets.search_project.subprocess.Popen", missing_rg
    )

    assert _project_search_results(workspace, "") == []
    assert _project_search_results(workspace, "50") == []
