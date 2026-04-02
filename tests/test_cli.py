import shlex
import subprocess
import sys
from pathlib import Path

from faltoobot.cli import app as cli
from faltoobot.config import Config


def make_config(tmp_path: Path) -> Config:
    home = tmp_path / "home"
    root = home / ".faltoobot"
    return Config(
        home=home,
        root=root,
        config_file=root / "config.toml",
        log_file=root / "faltoobot.log",
        sessions_dir=root / "sessions",
        session_db=root / "session.db",
        launch_agent=home / "Library" / "LaunchAgents" / "com.faltoobot.agent.plist",
        run_script=root / "run.sh",
        openai_api_key="",
        openai_oauth="",
        openai_model="gpt-5.4",
        openai_thinking="high",
        openai_fast=False,
        openai_transcription_model="gpt-4o-transcribe",
        allow_groups=False,
        allowed_chats=set(),
    )


def test_write_run_script_uses_current_python(tmp_path: Path) -> None:
    config = make_config(tmp_path)

    cli.write_run_script(config)

    text = config.run_script.read_text()
    assert text.startswith("#!/bin/sh\n")
    assert f"cd {shlex.quote(config.root.as_posix())}" in text
    assert f"exec {shlex.quote(sys.executable)} -m faltoobot.cli.app run" in text
    assert "-m faltoobot run" not in text


def test_write_systemd_service_redirects_to_log(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    cli.write_run_script(config)

    cli.write_systemd_service(config)

    text = cli.linux_service_file(config).read_text()
    assert "Description=Faltoobot WhatsApp bot" in text
    assert "WantedBy=default.target" in text
    assert shlex.quote(config.run_script.as_posix()) in text
    assert shlex.quote(config.log_file.as_posix()) in text


def test_install_service_uses_systemd_on_linux(tmp_path: Path, monkeypatch) -> None:
    config = make_config(tmp_path)
    calls: list[tuple[tuple[str, ...], bool]] = []

    def fake_systemctl(
        *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        calls.append((args, check))
        return subprocess.CompletedProcess(["systemctl", "--user", *args], 0, "", "")

    monkeypatch.setattr(cli.sys, "platform", "linux")
    monkeypatch.setattr(cli, "ensure_config_file", lambda: config.config_file)
    monkeypatch.setattr(cli, "run_systemctl", fake_systemctl)

    cli.install_service(config)

    assert config.run_script.exists()
    assert cli.linux_service_file(config).exists()
    assert calls == [
        (("daemon-reload",), True),
        (("enable", "--now", "faltoobot.service"), True),
        (("restart", "faltoobot.service"), True),
    ]


def test_has_service_checks_linux_unit_file(tmp_path: Path, monkeypatch) -> None:
    config = make_config(tmp_path)
    unit_file = cli.linux_service_file(config)
    unit_file.parent.mkdir(parents=True, exist_ok=True)
    unit_file.write_text("[Unit]\n", encoding="utf-8")

    monkeypatch.setattr(cli.sys, "platform", "linux")

    assert cli.has_service(config) is True


def test_render_log_line_uses_level_colors() -> None:
    assert cli.render_log_line("2026-03-17 INFO faltoobot: ok").style == "cyan"
    assert (
        cli.render_log_line(
            "13:03:05.809 [whatsmeow.Client.Socket WARNING] - noisy"
        ).style
        == "yellow"
    )
    assert cli.render_log_line("Traceback (most recent call last):").style == "bold red"


def test_render_config_includes_oauth_path() -> None:
    text = cli.render_config(
        {
            "openai": {
                "api_key": "",
                "oauth": "~/.faltoobot/auth.json",
                "model": "gpt-5.4",
                "thinking": "high",
                "fast": False,
                "transcription_model": "gpt-4o-mini-transcribe",
            },
            "bot": {
                "allow_groups": False,
                "allowed_chats": [],
            },
        }
    )

    assert 'oauth = "~/.faltoobot/auth.json"' in text


def test_render_config_includes_transcription_model() -> None:
    text = cli.render_config(
        {
            "openai": {
                "api_key": "",
                "model": "gpt-5.4",
                "thinking": "high",
                "fast": False,
                "transcription_model": "gpt-4o-mini-transcribe",
            },
            "bot": {
                "allow_groups": False,
                "allowed_chats": [],
            },
        }
    )

    assert 'transcription_model = "gpt-4o-mini-transcribe"' in text


def test_merge_config_clamps_invalid_transcription_model() -> None:
    merged = cli.merge_config(
        {
            "openai": {"transcription_model": "bad-model"},
            "bot": {},
        }
    )

    assert merged["openai"]["transcription_model"] == "gpt-4o-transcribe"


def test_handle_command_runs_login(monkeypatch, tmp_path: Path) -> None:
    config = make_config(tmp_path)
    calls: list[str] = []

    monkeypatch.setattr(
        cli, "run_openai_login", lambda console=None: calls.append("ran")
    )

    cli.handle_command(cli.argparse.Namespace(command="login"), config)

    assert calls == ["ran"]


def test_handle_command_runs_makemigrations(monkeypatch, tmp_path: Path) -> None:
    config = make_config(tmp_path)
    calls: list[str] = []

    monkeypatch.setattr(cli, "run_makemigrations_command", lambda: calls.append("ran"))

    cli.handle_command(cli.argparse.Namespace(command="makemigrations"), config)

    assert calls == ["ran"]
