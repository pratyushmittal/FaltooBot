import sys

import pytest

from faltoobot.cli.app import parse_args as parse_bot_args
from faltoobot.faltoochat.app import parse_args as parse_chat_args
from importlib.metadata import version as package_version


def test_faltoobot_version_flag(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["faltoobot", "--version"])

    with pytest.raises(SystemExit) as exc:
        parse_bot_args()

    assert exc.value.code == 0
    assert (
        capsys.readouterr().out.strip() == f"faltoobot {package_version('faltoobot')}"
    )


def test_faltoochat_version_flag(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["faltoochat", "--version"])

    with pytest.raises(SystemExit) as exc:
        parse_chat_args()

    assert exc.value.code == 0
    assert (
        capsys.readouterr().out.strip() == f"faltoochat {package_version('faltoobot')}"
    )
