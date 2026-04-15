from pathlib import Path

import pytest

from faltoobot.faltoochat.terminal import open_in_editor


def test_open_in_editor_prefers_nvim_with_line_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(
        "faltoobot.faltoochat.terminal.shutil.which",
        lambda name: "/usr/bin/nvim" if name == "nvim" else None,
    )
    monkeypatch.setattr(
        "faltoobot.faltoochat.terminal.subprocess.run",
        lambda command, check=False: seen.append(command),
    )

    assert open_in_editor(Path("alpha.py"), line_number=2) is True
    assert seen == [["/usr/bin/nvim", "+2", "alpha.py"]]


def test_open_in_editor_falls_back_to_vi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/usr/bin/vi" if name == "vi" else None

    monkeypatch.setattr("faltoobot.faltoochat.terminal.shutil.which", fake_which)
    monkeypatch.setattr(
        "faltoobot.faltoochat.terminal.subprocess.run",
        lambda command, check=False: seen.append(command),
    )

    assert open_in_editor(Path("alpha beta.py"), line_number=3) is True
    assert seen == [["/usr/bin/vi", "+3", "alpha beta.py"]]


def test_open_in_editor_uses_default_editor_when_no_terminal_editor_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Path] = []
    monkeypatch.setattr(
        "faltoobot.faltoochat.terminal.shutil.which", lambda _name: None
    )
    monkeypatch.setattr(
        "faltoobot.faltoochat.terminal.open_in_default_editor",
        lambda path: seen.append(path),
    )

    assert open_in_editor(Path("alpha.py")) is False
    assert seen == [Path("alpha.py")]
