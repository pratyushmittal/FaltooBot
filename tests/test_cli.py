import io
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

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
        allow_group_chats=set(),
        allowed_chats=set(),
        bot_name="Faltoo",
        browser_binary="",
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
    monkeypatch.setattr(
        cli, "_ensure_configured", lambda: ensured.append("ran") or config
    )
    crontab: list[str] = []
    monkeypatch.setattr(
        cli, "_ensure_crontab_path", lambda: crontab.append("ran") or True
    )
    reinstalls: list[str] = []
    monkeypatch.setattr(
        cli, "_run_migrations", lambda config: migrations.append("ran") or ["sessions"]
    )
    monkeypatch.setattr(cli, "_service_installed", lambda config: True)
    monkeypatch.setattr(
        cli, "_reinstall_service", lambda config: reinstalls.append("ran")
    )

    result = cli.run_update_command(config)

    assert calls == [("uv", "tool", "upgrade", "faltoobot")]
    assert ensured == ["ran"]
    assert crontab == ["ran"]
    assert migrations == ["ran"]
    assert reinstalls == ["ran"]
    assert result == config


def test_run_update_command_reexecs_when_new_version_was_installed(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    calls: list[tuple[str, ...]] = []
    ensured: list[str] = []
    versions = iter(["1.6.0", "1.6.1"])
    reexecs: list[str] = []

    monkeypatch.setattr(cli, "build_config", lambda: config)
    monkeypatch.setattr(cli, "_uv_bin", lambda: "uv")
    monkeypatch.setattr(cli, "_run_cmd", lambda *args: calls.append(args))
    monkeypatch.setattr(cli, "package_version", lambda name: next(versions))
    monkeypatch.setattr(
        cli, "_ensure_configured", lambda: ensured.append("ran") or config
    )
    reinstalls: list[str] = []
    monkeypatch.setattr(
        cli, "_reinstall_service", lambda config: reinstalls.append("ran")
    )
    monkeypatch.setattr(cli, "_reexec_current_command", lambda: reexecs.append("ran"))
    monkeypatch.setattr(
        cli,
        "_ensure_crontab_path",
        lambda: (_ for _ in ()).throw(AssertionError("should not run")),
    )

    result = cli.run_update_command(config)

    assert calls == [("uv", "tool", "upgrade", "faltoobot")]
    assert ensured == []
    assert reinstalls == []
    assert reexecs == ["ran"]
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


def test_run_configure_command_runs_selected_setup(tmp_path: Path, monkeypatch) -> None:
    config = make_config(tmp_path)
    calls: list[str] = []

    monkeypatch.setattr(cli, "_configure_openai", lambda config: calls.append("openai"))
    monkeypatch.setattr(
        cli, "_ensure_crontab_path", lambda: calls.append("cron") or True
    )
    monkeypatch.setattr(cli, "_restart_service", lambda config: calls.append("restart"))

    cli.run_configure_command(config, mode="openai")

    assert calls == ["openai", "cron", "restart"]


def test_run_configure_command_runs_gemini_setup(tmp_path: Path, monkeypatch) -> None:
    config = make_config(tmp_path)
    calls: list[str] = []

    monkeypatch.setattr(cli, "_configure_gemini", lambda config: calls.append("gemini"))
    monkeypatch.setattr(
        cli, "_ensure_crontab_path", lambda: calls.append("cron") or True
    )
    monkeypatch.setattr(cli, "_restart_service", lambda config: calls.append("restart"))

    cli.run_configure_command(config, mode="gemini")

    assert calls == ["gemini", "cron", "restart"]


def test_configure_gemini_saves_api_key(tmp_path: Path, monkeypatch) -> None:
    config = make_config(tmp_path)

    monkeypatch.setattr(cli, "_prompt_text", lambda *args, **kwargs: "gem-key")

    cli._configure_gemini(config)

    text = config.config_file.read_text(encoding="utf-8")
    assert "[gemini]" in text
    assert 'gemini_api_key = "gem-key"' in text
    assert 'model = "gemini-3.1-flash-image-preview"' in text


def test_run_notify_command_formats_message_with_source(monkeypatch) -> None:
    seen: dict[str, str | None] = {}
    monkeypatch.setattr(
        cli.notify_queue,
        "enqueue_notification",
        lambda chat_key, message, *, source=None: (
            seen.__setitem__("chat_key", chat_key)
            or seen.__setitem__("message", message)
            or seen.__setitem__("source", source)
            or "notify-1"
        ),
    )

    result = cli.run_notify_command(
        cli.argparse.Namespace(
            chat_key="code@demo",
            message="Hello from cron",
            source="cron:daily-ops",
        )
    )

    assert result == "notify-1"
    assert seen["chat_key"] == "code@demo"
    assert seen["source"] == "cron:daily-ops"
    message = seen["message"]
    assert message is not None
    assert "Hello from cron" in message


def test_run_notify_command_reads_message_from_stdin(monkeypatch) -> None:
    seen: dict[str, str | None] = {}
    monkeypatch.setattr(
        cli.notify_queue,
        "enqueue_notification",
        lambda chat_key, message, *, source=None: (
            seen.__setitem__("chat_key", chat_key)
            or seen.__setitem__("message", message)
            or seen.__setitem__("source", source)
            or "notify-1"
        ),
    )

    class FakeStdin(io.StringIO):
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr(cli.sys, "stdin", FakeStdin("Hello from stdin\n"))

    result = cli.run_notify_command(
        cli.argparse.Namespace(
            chat_key="code@demo",
            message=None,
            source="monitor:disk-usage",
        )
    )

    assert result == "notify-1"
    assert seen["chat_key"] == "code@demo"
    assert seen["source"] == "monitor:disk-usage"
    message = seen["message"]
    assert message is not None
    assert "Hello from stdin" in message


def test_run_browser_command_installs_playwright_chrome_when_binary_missing(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    calls: list[tuple[str, ...]] = []
    seen: dict[str, str] = {}

    monkeypatch.setattr(cli, "build_config", lambda: config)
    monkeypatch.setattr(cli, "_run_cmd", lambda *args: calls.append(args))
    installed = iter([None, "/tmp/chrome"])
    monkeypatch.setattr(
        cli.browser_runtime, "default_browser_binary", lambda: next(installed)
    )
    monkeypatch.setattr(
        cli.browser_runtime,
        "open_browser",
        lambda *, root, binary, url=None: seen.update(
            {"root": str(root), "binary": binary, "url": url or ""}
        ),
    )

    cli.run_browser_command(cli.argparse.Namespace(url="https://example.com"), config)

    assert calls == [(sys.executable, "-m", "playwright", "install", "chrome")]
    assert seen == {
        "root": str(config.root),
        "binary": "/tmp/chrome",
        "url": "https://example.com",
    }
    data = cli.merge_config(cli.load_toml(config.config_file))
    assert data["browser"]["binary"] == "/tmp/chrome"


def test_run_configure_command_browser_mode_installs_playwright_chrome(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(cli, "_run_cmd", lambda *args: calls.append(args))
    monkeypatch.setattr(
        cli.browser_runtime, "default_browser_binary", lambda: "/tmp/chrome"
    )
    monkeypatch.setattr(cli, "_prompt_menu", lambda *args, **kwargs: 1)
    monkeypatch.setattr(cli, "_restart_service", lambda config: None)

    cli.run_configure_command(config, mode="browser")

    assert calls == [(sys.executable, "-m", "playwright", "install", "chrome")]
    data = cli.merge_config(cli.load_toml(config.config_file))
    assert data["browser"]["binary"] == "/tmp/chrome"


def test_run_browser_command_rejects_missing_configured_binary(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    config.browser_binary = "/tmp/does-not-exist"

    try:
        cli.run_browser_command(cli.argparse.Namespace(url=None), config)
    except SystemExit as exc:
        assert str(exc) == "Configured browser binary not found: /tmp/does-not-exist"
    else:
        raise AssertionError("Expected SystemExit")


def test_ensure_configured_runs_missing_browser_setup_for_old_config(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    config.config_file.parent.mkdir(parents=True, exist_ok=True)
    config.config_file.write_text('[openai]\nmodel = "gpt-5.4"\n', encoding="utf-8")
    calls: list[str] = []

    monkeypatch.setattr(cli, "app_root", lambda: config.root)
    monkeypatch.setattr(cli, "build_config", lambda: config)
    monkeypatch.setattr(
        cli,
        "run_configure_command",
        lambda config, *, mode=None: calls.append(str(mode)),
    )

    result = cli._ensure_configured()

    assert calls == ["browser"]
    assert result == config


def test_ensure_configured_checks_missing_modes_before_build_config_migrates_file(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    config.config_file.parent.mkdir(parents=True, exist_ok=True)
    config.config_file.write_text(
        """[openai]
model = "gpt-5.4"
""",
        encoding="utf-8",
    )
    calls: list[str] = []

    def fake_build_config() -> Config:
        # comment: build_config migrates config.toml in real runs, so simulate it filling the
        # browser section before _ensure_configured returns.
        config.config_file.write_text(
            """[openai]
model = "gpt-5.4"

[browser]
binary = ""
""",
            encoding="utf-8",
        )
        return config

    monkeypatch.setattr(cli, "app_root", lambda: config.root)
    monkeypatch.setattr(cli, "build_config", fake_build_config)
    monkeypatch.setattr(
        cli,
        "run_configure_command",
        lambda config, *, mode=None: calls.append(str(mode)),
    )

    result = cli._ensure_configured()

    assert calls == ["browser"]
    assert result == config


def test_ensure_configured_skips_present_required_values(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    config.config_file.parent.mkdir(parents=True, exist_ok=True)
    config.config_file.write_text('[browser]\nbinary = ""\n', encoding="utf-8")
    calls: list[str] = []

    monkeypatch.setattr(cli, "app_root", lambda: config.root)
    monkeypatch.setattr(cli, "build_config", lambda: config)
    monkeypatch.setattr(
        cli,
        "run_configure_command",
        lambda config, *, mode=None: calls.append(str(mode)),
    )

    result = cli._ensure_configured()

    assert calls == []
    assert result == config


def test_run_whatsapp_auth_surfaces_libmagic_help(monkeypatch, tmp_path: Path) -> None:
    config = make_config(tmp_path)

    original_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "faltoobot.whatsapp.login":
            raise ImportError("failed to find libmagic. Check your installation")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(cli.sys, "platform", "darwin")
    monkeypatch.setattr("builtins.__import__", fake_import)

    try:
        cli._run_whatsapp_auth(config)
    except SystemExit as exc:
        assert str(exc) == (
            "WhatsApp support requires libmagic on macOS. Install it with `brew install libmagic` and rerun the command."
        )
    else:
        raise AssertionError("expected SystemExit")


def test_non_whatsapp_commands_do_not_import_whatsapp(monkeypatch) -> None:
    calls: list[str] = []

    def fake_update(config=None):
        calls.append("update")

    monkeypatch.setattr(cli, "run_update_command", fake_update)

    cli.handle_command(cli.argparse.Namespace(command="update"), None)

    assert calls == ["update"]


def test_run_allow_group_chats_command_adds_normalized_chats(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    restarts: list[Config] = []

    monkeypatch.setattr(cli, "build_config", lambda: config)
    monkeypatch.setattr(cli, "_restart_service", lambda config: restarts.append(config))

    result = cli.run_allow_group_chats_command(
        cli.argparse.Namespace(
            allow_group_chats_command="add",
            chats=["+1 (555) 123-4567", "15551234568@s.whatsapp.net"],
        ),
        config,
    )

    assert result == [
        "15551234567@s.whatsapp.net",
        "15551234568@s.whatsapp.net",
    ]
    data = cli.merge_config(cli.load_toml(config.config_file))
    assert data["bot"]["allow_group_chats"] == result
    assert restarts == [config]


def test_run_allow_group_chats_command_removes_chats(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    cli._write_config(
        {
            "bot": {
                "allow_group_chats": [
                    "15551234567@s.whatsapp.net",
                    "15551234568@s.whatsapp.net",
                ]
            }
        },
        config.config_file,
    )
    restarts: list[Config] = []

    monkeypatch.setattr(cli, "build_config", lambda: config)
    monkeypatch.setattr(cli, "_restart_service", lambda config: restarts.append(config))

    result = cli.run_allow_group_chats_command(
        cli.argparse.Namespace(
            allow_group_chats_command="remove",
            chats=["15551234567"],
        ),
        config,
    )

    assert result == ["15551234568@s.whatsapp.net"]
    data = cli.merge_config(cli.load_toml(config.config_file))
    assert data["bot"]["allow_group_chats"] == result
    assert restarts == [config]


def test_run_allow_group_chats_command_lists_without_restart(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    cli._write_config(
        {"bot": {"allow_group_chats": ["15551234567@s.whatsapp.net"]}},
        config.config_file,
    )
    seen: list[str] = []

    monkeypatch.setattr(cli.console, "print", lambda value="": seen.append(str(value)))
    monkeypatch.setattr(
        cli,
        "_restart_service",
        lambda config: (_ for _ in ()).throw(AssertionError("should not restart")),
    )

    result = cli.run_allow_group_chats_command(
        cli.argparse.Namespace(allow_group_chats_command="list"),
        config,
    )

    assert result == ["15551234567@s.whatsapp.net"]
    assert seen == ["15551234567@s.whatsapp.net"]


def test_handle_command_routes_allow_group_chats(monkeypatch, tmp_path: Path) -> None:
    config = make_config(tmp_path)
    calls: list[tuple[str, list[str]]] = []

    monkeypatch.setattr(
        cli,
        "run_allow_group_chats_command",
        lambda args, config=None: calls.append(
            (args.allow_group_chats_command, list(getattr(args, "chats", [])))
        ),
    )

    cli.handle_command(
        cli.argparse.Namespace(
            command="allow-group-chats",
            allow_group_chats_command="add",
            chats=["15551234567"],
        ),
        config,
    )

    assert calls == [("add", ["15551234567"])]


def test_crontab_path_value_appends_uv_bin() -> None:
    value = cli._crontab_path_value(Path("/tmp/uv-bin"), "/usr/bin:/bin")

    assert value == "/usr/bin:/bin:/tmp/uv-bin"


@pytest.mark.parametrize(
    ("crontab", "env_path", "expected_changed", "expected_written"),
    [
        pytest.param(
            "",
            "/usr/bin:/bin",
            True,
            ["PATH=/usr/bin:/bin:/tmp/uv-bin\n"],
            id="inserts-path-for-empty-crontab",
        ),
        pytest.param(
            "PATH=/usr/bin:/bin\n* * * * * faltoochat\n",
            None,
            True,
            ["PATH=/usr/bin:/bin:/tmp/uv-bin\n* * * * * faltoochat\n"],
            id="updates-existing-path",
        ),
        pytest.param(
            "PATH=/usr/bin:/bin:/tmp/uv-bin\n",
            None,
            False,
            [],
            id="skips-when-already-present",
        ),
    ],
)
def test_ensure_crontab_path(
    crontab: str,
    env_path: str | None,
    expected_changed: bool,
    expected_written: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_uv_tool_bin_dir", lambda: Path("/tmp/uv-bin"))
    monkeypatch.setattr(cli, "_load_crontab", lambda: crontab)
    if env_path is not None:
        monkeypatch.setattr(cli.os, "environ", {"PATH": env_path})
    written: list[str] = []
    monkeypatch.setattr(cli, "_write_crontab", lambda text: written.append(text))

    changed = cli._ensure_crontab_path()

    assert changed is expected_changed
    assert written == expected_written


def test_configure_whatsapp_defaults_group_allowlist_to_allowed_chats(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    prompts: list[tuple[str, list[str]]] = []
    confirms = iter([True, False])

    def fake_confirm(*args, **kwargs) -> bool:
        return next(confirms)

    def fake_prompt(current: list[str], *, label: str = "Allowed chats") -> list[str]:
        prompts.append((label, list(current)))
        if label == "Allowed chats":
            return ["15551234567@s.whatsapp.net"]
        return list(current)

    monkeypatch.setattr(cli.Confirm, "ask", fake_confirm)
    monkeypatch.setattr(cli, "_prompt_allowed_chats", fake_prompt)

    cli._configure_whatsapp(config)

    assert prompts == [
        ("Allowed chats", []),
        ("Allowed group chats", ["15551234567@s.whatsapp.net"]),
    ]
    data = cli.merge_config(cli.load_toml(config.config_file))
    assert data["bot"]["allowed_chats"] == ["15551234567@s.whatsapp.net"]
    assert data["bot"]["allow_group_chats"] == ["15551234567@s.whatsapp.net"]
