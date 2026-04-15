import sys
from collections.abc import Callable
from importlib.metadata import version as package_version

import pytest

from faltoobot.cli.app import parse_args as parse_bot_args
from faltoobot.faltoochat.app import parse_args as parse_chat_args


@pytest.mark.parametrize(
    ("argv0", "parser", "expected_name"),
    [
        pytest.param("faltoobot", parse_bot_args, "faltoobot", id="bot-version"),
        pytest.param("faltoochat", parse_chat_args, "faltoochat", id="chat-version"),
    ],
)
def test_version_flag(
    argv0: str,
    parser: Callable[[], object],
    expected_name: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", [argv0, "--version"])

    with pytest.raises(SystemExit) as exc:
        parser()

    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == (
        f"{expected_name} {package_version('faltoobot')}"
    )
