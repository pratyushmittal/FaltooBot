from __future__ import annotations

import argparse
import asyncio
import ctypes.util
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import time
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any, NoReturn

from rich.console import Console
from rich.text import Text

from faltoobot import notify_queue
from faltoobot.changelog import changelog_between, record_update
from faltoobot.cli import browser as browser_runtime
from faltoobot.config import (
    APP_LABEL,
    Config,
    build_config,
    ensure_config_file,
    load_toml,
    merge_config,
    migrate_config_file,
    render_config,
)
from faltoobot.migrate import main as run_migrations
from faltoobot.openai_login import run_openai_login

console = Console()
LOG_STYLES = {
    "ERROR": "bold red",
    "WARNING": "yellow",
    "INFO": "cyan",
    "DEBUG": "dim",
}
LINUX_SERVICE_NAME = "faltoobot.service"
SERVICE_COMMAND = "whatsapp-service"
PREVIOUS_VERSION_ENV = "FALTOOBOT_PREVIOUS_VERSION"
LINUX_LIBMAGIC_PACKAGE = "libmagic1"
DARWIN_LIBMAGIC_PACKAGE = "libmagic"

CRONTAB_DEFAULT_PATH_PARTS = [
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
    "/usr/local/bin",
]


def _require_service_platform() -> None:
    if sys.platform not in {"darwin", "linux"}:
        raise SystemExit("This command supports macOS and Linux only.")


def _uv_bin() -> str:
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("uv is required. Install it first: https://docs.astral.sh/uv/")
    return uv


def _run_cmd(*args: str) -> None:
    """Run a command and stream its output directly to the terminal."""
    subprocess.run(list(args), check=True, text=True)


def _sudo_prefix() -> list[str] | None:
    if os.geteuid() == 0:
        # comment: root can install system packages without sudo.
        return []
    sudo = shutil.which("sudo")
    if not sudo:
        # comment: minimal servers/containers may not include sudo.
        return None
    result = _run_capture(sudo, "-n", "true", check=False)
    return [sudo, "-n"] if result.returncode == 0 else None


def _install_with_apt(package: str) -> None:
    prefix = _sudo_prefix()
    if prefix is None:
        # comment: avoid prompting for sudo password from update/service setup.
        raise SystemExit(
            f"Install system dependency manually: sudo apt-get install -y {package}"
        )
    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
    subprocess.run([*prefix, "apt-get", "update"], check=True, text=True, env=env)
    subprocess.run(
        [*prefix, "apt-get", "install", "-y", package], check=True, text=True, env=env
    )


def _install_with_brew(package: str) -> None:
    brew = shutil.which("brew")
    if not brew:
        # comment: macOS installs need Homebrew unless the user installs libmagic manually.
        raise SystemExit(f"Install system dependency manually: brew install {package}")
    _run_cmd(brew, "install", package)


def _ensure_system_dependencies() -> list[str]:
    if ctypes.util.find_library("magic"):
        # comment: python-magic can import when the shared libmagic library is present.
        return []
    if sys.platform == "linux" and shutil.which("apt-get"):
        _install_with_apt(LINUX_LIBMAGIC_PACKAGE)
        return [LINUX_LIBMAGIC_PACKAGE]
    if sys.platform == "darwin":
        _install_with_brew(DARWIN_LIBMAGIC_PACKAGE)
        return [DARWIN_LIBMAGIC_PACKAGE]
    raise SystemExit(
        "Missing system dependency: libmagic. "
        f"On Ubuntu/Debian, run `sudo apt-get install -y {LINUX_LIBMAGIC_PACKAGE}`."
    )


def _reexec_current_command() -> NoReturn:
    """Replace this process with the freshly installed Faltoobot command."""
    os.execv(sys.executable, [sys.executable, "-m", "faltoobot.cli.app", *sys.argv[1:]])
    raise RuntimeError("os.execv returned unexpectedly")


def _run_capture(
    *args: str, check: bool = True, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a command and return its captured stdout/stderr to the caller."""
    return subprocess.run(
        list(args), check=check, text=True, capture_output=True, env=env
    )


def _reraised_whatsapp_import_error(exc: Exception) -> None:
    message = str(exc)
    if sys.platform == "darwin" and "libmagic" in message:
        raise SystemExit(
            "WhatsApp support requires libmagic on macOS. Install it with `brew install libmagic` and rerun the command."
        ) from exc
    raise exc


def _run_whatsapp_auth(config: Config) -> None:
    try:
        from faltoobot.whatsapp.login import run_auth
    except Exception as exc:
        _reraised_whatsapp_import_error(exc)
    asyncio.run(run_auth(config))


def _run_whatsapp_service(config: Config) -> None:
    try:
        from faltoobot.whatsapp.app import main as run_whatsapp_bot
    except Exception as exc:
        _reraised_whatsapp_import_error(exc)
    asyncio.run(run_whatsapp_bot(config))


def _uv_tool_bin_dir() -> Path:
    result = _run_capture(_uv_bin(), "tool", "dir", "--bin")
    return Path(result.stdout.strip())


def _crontab_path_value(uv_bin_dir: Path, current: str) -> str:
    # comment: cron PATH values are colon-separated, so keep only non-empty entries and
    # append the uv tool bin dir when it is not already present.
    parts = [part for part in current.split(":") if part]
    if not parts:
        # comment: fresh crontabs often have no PATH line at all, so start from a small
        # default system PATH before adding the uv tool bin dir.
        parts = list(CRONTAB_DEFAULT_PATH_PARTS)
    uv_bin = uv_bin_dir.as_posix()
    if uv_bin not in parts:
        parts.append(uv_bin)
    return ":".join(parts)


def _load_crontab() -> str:
    result = _run_capture("crontab", "-l", check=False)
    if result.returncode == 0:
        return result.stdout
    output = f"{result.stdout}\n{result.stderr}".lower()
    # comment: `crontab -l` exits non-zero when the user has no crontab yet, which is
    # expected on fresh systems.
    if "no crontab" in output:
        return ""
    message = (result.stderr or result.stdout).strip() or "crontab -l failed"
    raise subprocess.SubprocessError(message)


def _write_crontab(text: str) -> None:
    subprocess.run(["crontab", "-"], input=text, check=True, text=True)


def _ensure_crontab_path() -> bool:
    """Best-effort add the uv tool bin dir to PATH in the user's crontab."""
    try:
        uv_bin_dir = _uv_tool_bin_dir()
        crontab_text = _load_crontab()
    except (OSError, subprocess.SubprocessError) as exc:
        console.print(f"[dim]Skipping crontab PATH update: {exc}[/]")
        return False

    lines = crontab_text.splitlines()
    changed = False
    found_path = False
    for index, line in enumerate(lines):
        if not line.startswith("PATH="):
            continue
        found_path = True
        updated = _crontab_path_value(uv_bin_dir, line.split("=", 1)[1])
        if updated == line.split("=", 1)[1]:
            continue
        lines[index] = f"PATH={updated}"
        changed = True
    if not found_path:
        lines.insert(
            0, f"PATH={_crontab_path_value(uv_bin_dir, os.environ.get('PATH', ''))}"
        )
        changed = True
    if not changed:
        return False
    text = "\n".join(lines)
    if text and not text.endswith("\n"):
        text += "\n"
    _write_crontab(text)
    console.print(f"[dim]Updated crontab PATH with [cyan]{uv_bin_dir}[/][/]")
    return True


def _uid() -> str:
    return str(os.getuid())


def _darwin_service_target() -> str:
    """Return the `launchctl` target name for the current macOS user session."""
    return f"gui/{_uid()}/{APP_LABEL}"


def _linux_service_file(config: Config) -> Path:
    return config.home / ".config" / "systemd" / "user" / LINUX_SERVICE_NAME


def _service_file(config: Config) -> Path:
    if sys.platform == "darwin":
        return config.launch_agent
    return _linux_service_file(config)


def _service_installed(config: Config) -> bool:
    if sys.platform not in {"darwin", "linux"}:
        return False
    return _service_file(config).exists()


def _run_entrypoint() -> list[str]:
    """Return the foreground bot command used by both macOS and Linux services."""
    return [sys.executable, "-m", "faltoobot.cli.app", SERVICE_COMMAND]


def _shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def _write_run_script(config: Config) -> None:
    config.root.mkdir(parents=True, exist_ok=True)
    config.run_script.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                f"cd {shlex.quote(config.root.as_posix())}",
                f"exec {_shell_join(_run_entrypoint())}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    config.run_script.chmod(0o700)


def _write_darwin_launch_agent(config: Config) -> None:
    config.launch_agent.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "Label": APP_LABEL,
        "ProgramArguments": [config.run_script.as_posix()],
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(config.root),
        "StandardOutPath": str(config.log_file),
        "StandardErrorPath": str(config.log_file),
    }
    config.launch_agent.write_bytes(plistlib.dumps(data))


def _systemd_command(config: Config) -> str:
    return f"exec {shlex.quote(config.run_script.as_posix())} >> {shlex.quote(config.log_file.as_posix())} 2>&1"


def _write_systemd_service(config: Config) -> None:
    unit_file = _linux_service_file(config)
    unit_file.parent.mkdir(parents=True, exist_ok=True)
    unit_file.write_text(
        "\n".join(
            [
                "[Unit]",
                "Description=Faltoobot WhatsApp bot",
                "",
                "[Service]",
                "Type=simple",
                "Environment=PYTHONUNBUFFERED=1",
                f"ExecStart=/bin/sh -lc {shlex.quote(_systemd_command(config))}",
                "Restart=always",
                "RestartSec=2",
                "",
                "[Install]",
                "WantedBy=default.target",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _run_darwin_launchctl(
    *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return _run_capture("launchctl", *args, check=check)


def _systemctl_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{_uid()}")
    return env


def _run_systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return _run_capture(
            "systemctl", "--user", *args, check=check, env=_systemctl_env()
        )
    except (
        FileNotFoundError
    ) as exc:  # comment: systemctl is required for Linux services.
        raise SystemExit("systemctl is required on Linux.") from exc


def _install_service(config: Config) -> None:
    _require_service_platform()
    ensure_config_file()
    _write_run_script(config)
    if sys.platform == "darwin":
        _write_darwin_launch_agent(config)
        return
    _write_systemd_service(config)
    _run_systemctl("daemon-reload")
    _run_systemctl("enable", LINUX_SERVICE_NAME)


def _stop_service(config: Config) -> None:
    if not _service_installed(config):
        return
    _require_service_platform()
    if sys.platform == "darwin":
        _run_darwin_launchctl(
            "bootout", f"gui/{_uid()}", config.launch_agent.as_posix(), check=False
        )
        return
    _run_systemctl("stop", LINUX_SERVICE_NAME, check=False)


def _start_service(config: Config) -> None:
    _require_service_platform()
    if sys.platform == "darwin":
        _run_darwin_launchctl(
            "bootstrap", f"gui/{_uid()}", config.launch_agent.as_posix()
        )
        _run_darwin_launchctl("enable", _darwin_service_target(), check=False)
        _run_darwin_launchctl("kickstart", "-k", _darwin_service_target())
        return
    _run_systemctl("start", LINUX_SERVICE_NAME)


def _reinstall_service(config: Config) -> None:
    _stop_service(config)
    _install_service(config)
    _start_service(config)


def _log_style(line: str) -> str:
    if "Traceback" in line or "Exception" in line:
        return "bold red"
    for level, style in LOG_STYLES.items():
        markers = (
            f" {level} ",
            f" {level}]",
            f"[{level}]",
            f"] - {level}",
            f": {level} ",
        )
        if any(marker in line for marker in markers):
            return style
    return ""


def _render_log_line(line: str) -> Text:
    return Text(line.rstrip("\n"), style=_log_style(line))


def _tail_file(path: Path, *, lines: int = 100, follow: bool = True) -> None:
    if not path.exists():
        console.print(f"[yellow]No log file at[/] [cyan]{path}[/]")
        return
    data = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in data[-lines:]:
        console.print(_render_log_line(line))
    if not follow:
        return
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(0, os.SEEK_END)
        while True:
            line = handle.readline()
            if line:
                console.print(_render_log_line(line))
                continue
            time.sleep(0.5)


def _write_config(data: dict[str, dict[str, Any]], config_file: Path) -> None:
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(render_config(data), encoding="utf-8")


def _install_playwright_chrome() -> str:
    _run_cmd(sys.executable, "-m", "playwright", "install", "chrome")
    binary = browser_runtime.default_browser_binary()
    if binary:
        return binary
    raise SystemExit(
        "Playwright installed Chrome, but Faltoobot could not find the browser binary."
    )


def _ensure_browser_binary(config: Config) -> str:
    if config.browser_binary:
        if Path(config.browser_binary).exists():
            return config.browser_binary
        raise SystemExit(
            f"Configured browser binary not found: {config.browser_binary}"
        )
    if binary := browser_runtime.default_browser_binary():
        data = merge_config(load_toml(config.config_file))
        data["browser"]["binary"] = binary
        _write_config(data, config.config_file)
        return binary
    console.print()
    console.print("[cyan]Installing Playwright Chrome for browser use...[/]")
    binary = _install_playwright_chrome()
    data = merge_config(load_toml(config.config_file))
    data["browser"]["binary"] = binary
    _write_config(data, config.config_file)
    return binary


def run_browser_command(args: argparse.Namespace, config: Config | None = None) -> None:
    config = config or build_config()
    binary = _ensure_browser_binary(config)
    browser_runtime.open_browser(root=config.root, binary=binary, url=args.url)


def _run_migrations(
    config: Config,
    *,
    previous_version: str | None = None,
    current_version: str | None = None,
) -> list[str]:
    changes: list[str] = []
    if migrate_config_file(config.config_file):
        changes.append("config")
    created_sessions_dir = not config.sessions_dir.exists()
    config.sessions_dir.mkdir(parents=True, exist_ok=True)
    # comment: report session setup only when update created the root directory.
    if created_sessions_dir:
        changes.append("sessions")
    changes.extend(
        run_migrations(
            config, previous_version=previous_version, current_version=current_version
        )
    )
    return changes


def show_logs(config: Config | None = None) -> None:
    config = config or build_config()
    _tail_file(config.log_file, follow=True)


def run_update_command(config: Config | None = None) -> Config | None:
    """Run `faltoobot update`."""
    _config = config or build_config()

    previous_version = package_version("faltoobot")
    _run_cmd(_uv_bin(), "tool", "upgrade", "faltoobot")
    current_version = package_version("faltoobot")

    # comment: finish setup in the newly installed code after uv upgrades the tool.
    if current_version != previous_version:
        os.environ[PREVIOUS_VERSION_ENV] = previous_version
        console.print(
            "[yellow]Faltoobot was upgraded.[/] "
            f"Restarting into [cyan]{current_version}[/] to finish the update."
        )
        _reexec_current_command()
        return None

    system_changes = _ensure_system_dependencies()
    if system_changes:
        console.print(
            f"[green]Installed system dependencies:[/] {', '.join(system_changes)}"
        )
    else:
        console.print("[dim]No required system dependencies to install.[/]")

    # comment: build_config creates ~/.faltoobot/config.toml with defaults when missing.
    config = build_config()
    console.print(f"[dim]Config:[/] [cyan]{config.config_file}[/]")

    _ensure_crontab_path()
    upgrade_from_version = os.environ.pop(PREVIOUS_VERSION_ENV, previous_version)
    changes = _run_migrations(
        config, previous_version=upgrade_from_version, current_version=current_version
    )
    record_update(upgrade_from_version, current_version)
    if upgrade_from_version != current_version:
        notify_queue.enqueue_notification(
            "global",
            changelog_between(upgrade_from_version, current_version),
            source="update:faltoobot",
            global_notification=True,
        )
    final_config = build_config()

    # comment: reinstall stops the old service, rewrites files, then starts it again.
    if _service_installed(final_config):
        _reinstall_service(final_config)

    summary = ", ".join(changes) if changes else "none"
    console.print(f"[green]Update complete.[/] changes: {summary}")
    return final_config


def run_whatsapp_command(config: Config | None = None) -> None:
    """Run `faltoobot whatsapp`."""
    config = run_update_command(config)
    if config is None:
        return
    _reinstall_service(config)
    console.print("[green]WhatsApp service is running.[/]")
    console.print("[dim]Press Ctrl+C any time. The service will keep running.[/]")
    console.print("logs: [cyan]faltoobot logs[/]")
    show_logs(config)


def run_notify_command(args: argparse.Namespace) -> str:
    message = notify_queue.parse_message(args.message, sys.stdin)
    notification_id = notify_queue.enqueue_notification(
        args.chat_key,
        message,
        source=str(args.source),
    )
    console.print(notification_id)
    return notification_id


def parse_args() -> argparse.Namespace:
    # comment: keep the service entrypoint hidden from public help while still accepting it.
    if sys.argv[1:2] == [SERVICE_COMMAND]:
        return argparse.Namespace(command=SERVICE_COMMAND)

    parser = argparse.ArgumentParser(prog="faltoobot")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {package_version('faltoobot')}",
    )
    sub = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{update,whatsapp,logs,browser,notify,codex-login}",
    )

    sub.add_parser(
        "update",
        help="upgrade faltoobot, refresh crontab PATH, and run setup tasks",
    )
    sub.add_parser(
        "whatsapp", help="run update, refresh the WhatsApp service, and follow logs"
    )

    sub.add_parser("logs", help="show logs in follow mode")
    browser = sub.add_parser("browser", help="launch a persistent browser with CDP")
    browser.add_argument("url", nargs="?", help="optional URL to open")
    notify = sub.add_parser("notify", help="enqueue a notification for a chat")
    notify.add_argument("chat_key", help="chat key to notify")
    notify.add_argument(
        "message", nargs="?", help="notification message, or read from stdin"
    )
    notify.add_argument(
        "--source",
        default="notify",
        help="identifier explaining why this notification was sent",
    )
    sub.add_parser("codex-login", help="sign in to Codex / ChatGPT OAuth")
    return parser.parse_args()


def handle_command(args: argparse.Namespace, config: Config | None = None) -> None:
    # comment: the public `whatsapp` command manages updates, service install/start, and log
    # following. The OS service itself needs a separate hidden entrypoint that only runs the bot.
    if args.command == SERVICE_COMMAND:
        _run_whatsapp_service(config or build_config())
    elif args.command == "update":
        run_update_command(config)
    elif args.command == "whatsapp":
        run_whatsapp_command(config)
    elif args.command == "logs":
        show_logs(config)
    elif args.command == "browser":
        run_browser_command(args, config)
    elif args.command == "notify":
        run_notify_command(args)
    elif args.command == "codex-login":
        run_openai_login(console)
    else:
        # comment: argparse keeps this unreachable unless the command table changes unexpectedly.
        raise SystemExit(f"unknown command: {args.command}")


def main() -> None:
    handle_command(parse_args())


if __name__ == "__main__":
    main()
