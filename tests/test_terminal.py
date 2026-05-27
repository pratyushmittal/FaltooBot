import sys
from pathlib import Path

import pytest

from faltoobot.faltoochat.terminal import open_in_editor, set_terminal_title


@pytest.mark.parametrize(
    ("available", "path", "line_number", "expected_command"),
    [
        (
            {"nvim": "/usr/bin/nvim"},
            Path("alpha.py"),
            2,
            ["/usr/bin/nvim", "+2", "alpha.py"],
        ),
        (
            {"emacs": "/usr/bin/emacs", "vi": "/usr/bin/vi"},
            Path("alpha beta.py"),
            3,
            ["/usr/bin/emacs", "+3", "alpha beta.py"],
        ),
    ],
)
def test_open_in_editor_uses_first_available_terminal_editor(
    available: dict[str, str],
    path: Path,
    line_number: int,
    expected_command: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        "faltoobot.faltoochat.terminal.shutil.which",
        lambda name: available.get(name),
    )
    monkeypatch.setattr(
        "faltoobot.faltoochat.terminal.subprocess.run",
        lambda command, check: seen.append(command),
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


class FakeTty:
    def __init__(self, fd: int, *, tty: bool = True) -> None:
        self.fd = fd
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty

    def fileno(self) -> int:
        return self.fd


def test_set_terminal_title_uses_real_stdout_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[tuple[int, bytes]] = []
    monkeypatch.setattr(sys, "stdout", FakeTty(1))
    monkeypatch.setattr(sys, "__stdout__", FakeTty(2))

    def fake_write(fd: int, data: bytes) -> int:
        if fd == 1:
            # comment: Textual tests can leave sys.stdout pointing at a closed pty.
            raise OSError
        writes.append((fd, data))
        return len(data)

    monkeypatch.setattr("faltoobot.faltoochat.terminal.os.write", fake_write)

    set_terminal_title("project")

    assert writes == [(2, b"\033]2;project\033\\")]


def test_set_terminal_title_skips_non_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[tuple[int, bytes]] = []
    monkeypatch.setattr(sys, "stdout", FakeTty(1, tty=False))
    monkeypatch.setattr(sys, "__stdout__", FakeTty(2, tty=False))
    monkeypatch.setattr(
        "faltoobot.faltoochat.terminal.os.write",
        lambda fd, data: writes.append((fd, data)) or len(data),
    )

    set_terminal_title("project")

    assert writes == []
