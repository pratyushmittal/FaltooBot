from pathlib import Path

import pytest

from faltoobot.faltoochat.terminal import open_in_editor


@pytest.mark.parametrize(
    ("available", "path", "line_number", "expected_command"),
    [
        pytest.param(
            {"nvim": "/usr/bin/nvim"},
            Path("alpha.py"),
            2,
            "/usr/bin/nvim +2 alpha.py",
            id="prefers-nvim",
        ),
        pytest.param(
            {"emacs": "/usr/bin/emacs", "vi": "/usr/bin/vi"},
            Path("alpha beta.py"),
            3,
            "/usr/bin/emacs +3 'alpha beta.py'",
            id="falls-back-to-emacs-before-vi",
        ),
    ],
)
def test_open_in_editor_uses_first_available_terminal_editor(
    available: dict[str, str],
    path: Path,
    line_number: int,
    expected_command: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        "faltoobot.faltoochat.terminal.shutil.which",
        lambda name: available.get(name),
    )
    monkeypatch.setattr(
        "faltoobot.faltoochat.terminal.os.system",
        lambda command: seen.append(command) or 0,
    )

    assert open_in_editor(path, line_number=line_number) is True
    assert seen == [expected_command]


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
