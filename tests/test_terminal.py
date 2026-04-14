from pathlib import Path

import pytest

from faltoobot.faltoochat.terminal import open_in_vi


def test_open_in_vi_uses_interactive_shell_for_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setattr(
        "faltoobot.faltoochat.terminal.subprocess.run",
        lambda command, check=False: seen.append(command),
    )

    assert open_in_vi(Path("alpha.py"), line_number=2) is None
    assert seen == [["/bin/bash", "-ic", "vi +2 alpha.py"]]


def test_open_in_vi_quotes_paths_when_using_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setattr(
        "faltoobot.faltoochat.terminal.subprocess.run",
        lambda command, check=False: seen.append(command),
    )

    assert open_in_vi(Path("alpha beta.py")) is None
    assert seen == [["/bin/bash", "-ic", "vi 'alpha beta.py'"]]
