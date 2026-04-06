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
        bot_name="Faltoo",
    )


def test_write_run_script_uses_whatsapp_service(tmp_path: Path) -> None:
    config = make_config(tmp_path)

    cli._write_run_script(config)

    text = config.run_script.read_text()
    assert text.startswith("#!/bin/sh\n")
    assert f"cd {shlex.quote(config.root.as_posix())}" in text
    assert (
        f"exec {shlex.quote(sys.executable)} -m faltoobot.cli.app {cli.SERVICE_COMMAND}"
        in text
    )


def test_write_systemd_service_redirects_to_log(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    cli._write_run_script(config)

    cli._write_systemd_service(config)

    text = cli._linux_service_file(config).read_text()
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
    monkeypatch.setattr(cli, "_run_systemctl", fake_systemctl)

    cli._install_service(config)

    assert config.run_script.exists()
    assert cli._linux_service_file(config).exists()
    assert calls == [
        (("daemon-reload",), True),
        (("enable", "faltoobot.service"), True),
    ]


def test_render_log_line_uses_level_colors() -> None:
    assert cli._render_log_line("2026-03-17 INFO faltoobot: ok").style == "cyan"
    assert (
        cli._render_log_line(
            "13:03:05.809 [whatsmeow.Client.Socket WARNING] - noisy"
        ).style
        == "yellow"
    )
    assert (
        cli._render_log_line("Traceback (most recent call last):").style == "bold red"
    )


def test_copy_bundled_skills_overwrites_existing_files(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    source = tmp_path / "source-skills"
    source.mkdir(parents=True)
    (source / "alpha.md").write_text("new alpha", encoding="utf-8")
    nested = source / "nested"
    nested.mkdir()
    (nested / "beta.md").write_text("new beta", encoding="utf-8")

    target = config.root / "skills"
    target.mkdir(parents=True)
    (target / "alpha.md").write_text("old alpha", encoding="utf-8")

    package_root = tmp_path / "package" / "faltoobot" / "cli"
    package_root.mkdir(parents=True)
    monkeypatch.setattr(cli, "__file__", str(package_root / "app.py"))
    target_source = package_root.parent / "skills"
    if target_source.exists():
        raise AssertionError("test source dir must start missing")
    source.replace(target_source)

    cli._copy_bundled_skills(config)

    assert (target / "alpha.md").read_text(encoding="utf-8") == "new alpha"
    assert (target / "nested" / "beta.md").read_text(encoding="utf-8") == "new beta"


def test_run_update_command_upgrades_then_bootstraps(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    calls: list[tuple[str, ...]] = []
    ensured: list[str] = []
    migrations: list[str] = []
    versions = iter(["1.6.0", "1.6.0"])

    monkeypatch.setattr(cli, "build_config", lambda: config)
    monkeypatch.setattr(cli, "_uv_bin", lambda: "uv")
    monkeypatch.setattr(cli, "_run_cmd", lambda *args: calls.append(args))
    monkeypatch.setattr(cli, "package_version", lambda name: next(versions))
    copied: list[str] = []
    monkeypatch.setattr(
        cli, "_ensure_configured", lambda: ensured.append("ran") or config
    )
    reinstalls: list[str] = []
    monkeypatch.setattr(
        cli, "_run_migrations", lambda config: migrations.append("ran") or ["sessions"]
    )
    monkeypatch.setattr(
        cli, "_copy_bundled_skills", lambda config: copied.append("ran")
    )
    monkeypatch.setattr(cli, "_service_installed", lambda config: True)
    monkeypatch.setattr(
        cli, "_reinstall_service", lambda config: reinstalls.append("ran")
    )

    result = cli.run_update_command(config)

    assert calls == [("uv", "tool", "upgrade", "faltoobot")]
    assert ensured == ["ran"]
    assert copied == ["ran"]
    assert migrations == ["ran"]
    assert reinstalls == ["ran"]
    assert result == config


def test_run_update_command_stops_when_new_version_was_installed(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    calls: list[tuple[str, ...]] = []
    ensured: list[str] = []
    versions = iter(["1.6.0", "1.6.1"])

    monkeypatch.setattr(cli, "build_config", lambda: config)
    monkeypatch.setattr(cli, "_uv_bin", lambda: "uv")
    monkeypatch.setattr(cli, "_run_cmd", lambda *args: calls.append(args))
    monkeypatch.setattr(cli, "package_version", lambda name: next(versions))
    copied: list[str] = []
    monkeypatch.setattr(
        cli, "_ensure_configured", lambda: ensured.append("ran") or config
    )
    reinstalls: list[str] = []
    monkeypatch.setattr(
        cli, "_copy_bundled_skills", lambda config: copied.append("ran")
    )
    monkeypatch.setattr(
        cli, "_reinstall_service", lambda config: reinstalls.append("ran")
    )

    result = cli.run_update_command(config)

    assert calls == [("uv", "tool", "upgrade", "faltoobot")]
    assert copied == []
    assert ensured == []
    assert reinstalls == []
    assert result is None


def test_run_whatsapp_command_runs_service_flow(tmp_path: Path, monkeypatch) -> None:
    config = make_config(tmp_path)
    calls: list[str] = []

    monkeypatch.setattr(cli, "run_update_command", lambda config=None: config)
    monkeypatch.setattr(
        cli, "_reinstall_service", lambda config: calls.append("reinstall")
    )
    monkeypatch.setattr(cli, "show_logs", lambda config=None: calls.append("logs"))

    cli.run_whatsapp_command(config)

    assert calls == ["reinstall", "logs"]


def test_handle_command_runs_makemigrations(monkeypatch, tmp_path: Path) -> None:
    config = make_config(tmp_path)
    calls: list[str] = []

    monkeypatch.setattr(cli, "run_makemigrations_command", lambda: calls.append("ran"))

    cli.handle_command(cli.argparse.Namespace(command="makemigrations"), config)

    assert calls == ["ran"]


def test_run_configure_command_copies_bundled_skills(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    calls: list[str] = []

    monkeypatch.setattr(
        cli, "_copy_bundled_skills", lambda config: calls.append("copy")
    )
    monkeypatch.setattr(cli, "_configure_openai", lambda config: calls.append("openai"))
    monkeypatch.setattr(cli, "_restart_service", lambda config: calls.append("restart"))

    cli.run_configure_command(config, mode="openai")

    assert calls == ["copy", "openai", "restart"]
