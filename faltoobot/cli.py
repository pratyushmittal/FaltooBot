import argparse
import asyncio
import getpass
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

from faltoobot.bot import run_auth, run_bot
from faltoobot.config import (
    APP_LABEL,
    DEFAULT_THINKING,
    MODEL_OPTIONS,
    THINKING_OPTIONS,
    Config,
    build_config,
    ensure_config_file,
    load_toml,
    merge_config,
    migrate_config_file,
    normalize_chat,
    render_config,
)
from faltoobot.store import ensure_sessions_dir

console = Console()


LOG_STYLES = {
    "ERROR": "bold red",
    "WARNING": "yellow",
    "INFO": "cyan",
    "DEBUG": "dim",
}


def require_service_platform() -> None:
    if sys.platform not in {"darwin", "linux"}:
        raise SystemExit("This command supports macOS and Linux only.")


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def uid() -> str:
    return str(os.getuid())


def service_target() -> str:
    return f"gui/{uid()}/{APP_LABEL}"


def linux_service_name() -> str:
    return "faltoobot.service"


def linux_service_file(config: Config) -> Path:
    return config.home / ".config" / "systemd" / "user" / linux_service_name()


def service_file(config: Config) -> Path:
    if sys.platform == "darwin":
        return config.launch_agent
    return linux_service_file(config)


def run_entrypoint() -> list[str]:
    return [sys.executable, "-m", "faltoobot", "run"]


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def write_run_script(config: Config) -> None:
    config.root.mkdir(parents=True, exist_ok=True)
    config.run_script.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                f"cd {shlex.quote(config.root.as_posix())}",
                f"exec {shell_join(run_entrypoint())}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    config.run_script.chmod(0o755)


def write_launch_agent(config: Config) -> None:
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


def systemd_command(config: Config) -> str:
    return f"exec {shlex.quote(config.run_script.as_posix())} >> {shlex.quote(config.log_file.as_posix())} 2>&1"


def write_systemd_service(config: Config) -> None:
    unit_file = linux_service_file(config)
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
                f"ExecStart=/bin/sh -lc {shlex.quote(systemd_command(config))}",
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


def run_launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *args], check=check, text=True, capture_output=True
    )


def run_cmd(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(list(args), check=True, text=True, cwd=cwd)


def run_systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["systemctl", "--user", *args],
            check=check,
            text=True,
            capture_output=True,
        )
    except (
        FileNotFoundError
    ) as exc:  # comment: systemctl is required for Linux background installs.
        raise SystemExit(
            "systemctl is required for `faltoobot install` on Linux."
        ) from exc


def read_cmd(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        list(args), check=True, text=True, cwd=cwd, capture_output=True
    )
    return result.stdout


def uv_bin() -> str:
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("uv is required. Install it first: https://docs.astral.sh/uv/")
    return uv


def has_service(config: Config) -> bool:
    if sys.platform not in {"darwin", "linux"}:
        return False
    return service_file(config).exists()


async def run_migrations(config: Config) -> list[str]:
    changes: list[str] = []
    if migrate_config_file(config.config_file):
        changes.append("config")
    ensure_sessions_dir(config.sessions_dir)
    changes.append("sessions")
    if has_service(config):
        install_service(config)
        changes.append("service")
    return changes


def update_app(config: Config, migrate_only: bool) -> None:
    if migrate_only:
        changes = asyncio.run(run_migrations(config))
        console.print(f"[green]Migrations:[/] {', '.join(changes)}")
        return

    repo = project_root()
    if not (repo / ".git").exists():
        raise SystemExit("`faltoobot update` only works from a git clone of the repo.")
    status = read_cmd("git", "status", "--short", cwd=repo).strip()
    if status:
        raise SystemExit(
            "Commit or stash local changes before running `faltoobot update`."
        )
    run_cmd("git", "pull", "--ff-only", cwd=repo)
    run_cmd(uv_bin(), "sync", cwd=repo)
    run_cmd(uv_bin(), "run", "faltoobot", "update", "--migrate-only", cwd=repo)


def install_service(config: Config) -> None:
    require_service_platform()
    ensure_config_file()
    config.root.mkdir(parents=True, exist_ok=True)
    write_run_script(config)
    if sys.platform == "darwin":
        write_launch_agent(config)
        run_launchctl(
            "bootout", f"gui/{uid()}", config.launch_agent.as_posix(), check=False
        )
        run_launchctl("bootstrap", f"gui/{uid()}", config.launch_agent.as_posix())
        run_launchctl("enable", service_target(), check=False)
        run_launchctl("kickstart", "-k", service_target())
    else:
        write_systemd_service(config)
        run_systemctl("daemon-reload")
        run_systemctl("enable", "--now", linux_service_name())
        run_systemctl("restart", linux_service_name())
    console.print(f"[green]Installed[/] {APP_LABEL}")
    console.print(f"service: [cyan]{service_file(config)}[/]")
    console.print(f"config: [cyan]{config.config_file}[/]")
    console.print(f"logs: [cyan]{config.log_file}[/]")


def uninstall_service(config: Config) -> None:
    require_service_platform()
    if sys.platform == "darwin":
        run_launchctl(
            "bootout", f"gui/{uid()}", config.launch_agent.as_posix(), check=False
        )
        if config.launch_agent.exists():
            config.launch_agent.unlink()
    else:
        run_systemctl("disable", "--now", linux_service_name(), check=False)
        unit_file = linux_service_file(config)
        if unit_file.exists():
            unit_file.unlink()
        run_systemctl("daemon-reload", check=False)
    if config.run_script.exists():
        config.run_script.unlink()
    console.print(f"[green]Removed[/] {APP_LABEL}")


def service_status(config: Config) -> None:
    require_service_platform()
    if sys.platform == "darwin":
        result = run_launchctl("print", service_target(), check=False)
        if result.returncode == 0:
            console.print(f"[green]{APP_LABEL}: loaded[/]")
            return
        console.print(f"[yellow]{APP_LABEL}: not loaded[/]")
    else:
        result = run_systemctl("is-active", linux_service_name(), check=False)
        if result.returncode == 0 and result.stdout.strip() == "active":
            console.print(f"[green]{linux_service_name()}: active[/]")
            return
        console.print(f"[yellow]{linux_service_name()}: inactive[/]")
    if service_file(config).exists():
        console.print(f"service: [cyan]{service_file(config)}[/]")


def log_style(line: str) -> str:
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


def render_log_line(line: str) -> Text:
    return Text(line.rstrip("\n"), style=log_style(line))


def tail_file(path: Path, lines: int = 100, follow: bool = False) -> None:
    if not path.exists():
        console.print(f"[yellow]No log file at[/] [cyan]{path}[/]")
        return
    data = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in data[-lines:]:
        console.print(render_log_line(line))
    if not follow:
        return
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(0, os.SEEK_END)
        while True:
            line = handle.readline()
            if line:
                console.print(render_log_line(line))
                continue
            time.sleep(0.5)


def prompt_text(label: str, current: str, *, secret: bool = False) -> str:
    current_text = (
        "[set]" if secret and current else f"[{current}]" if current else "[empty]"
    )
    raw = (
        getpass.getpass(f"{label} {current_text} (blank keeps current): ")
        if secret
        else console.input(f"[bold]{label}[/] {current_text} (blank keeps current): ")
    ).strip()
    if not raw:
        return current
    return "" if raw == "-" else raw


def prompt_bool(label: str, current: bool) -> bool:
    current_text = "y" if current else "n"
    while True:
        raw = (
            console.input(f"[bold]{label}[/] [y/n] (blank keeps {current_text}): ")
            .strip()
            .lower()
        )
        if not raw:
            return current
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        console.print("[yellow]Enter y or n.[/]")


def prompt_model(current: str) -> str:
    console.print("[bold]OpenAI model[/]")
    for index, model in enumerate(MODEL_OPTIONS, start=1):
        current_marker = " (current)" if model == current else ""
        console.print(f"  [cyan]{index}.[/] {model}{current_marker}")
    custom_index = len(MODEL_OPTIONS) + 1
    custom_marker = " (current)" if current and current not in MODEL_OPTIONS else ""
    console.print(f"  [cyan]{custom_index}.[/] custom{custom_marker}")
    while True:
        default_choice = (
            str(MODEL_OPTIONS.index(current) + 1)
            if current in MODEL_OPTIONS
            else str(custom_index)
        )
        raw = console.input(f"Select model [{default_choice}]: ").strip()
        if not raw:
            choice = default_choice
        else:
            choice = raw
        if choice.isdigit():
            index = int(choice)
            if 1 <= index <= len(MODEL_OPTIONS):
                return MODEL_OPTIONS[index - 1]
            if index == custom_index:
                return prompt_text(
                    "Custom model", current if current not in MODEL_OPTIONS else ""
                )
        console.print(f"[yellow]Enter a number between 1 and {custom_index}.[/]")


def prompt_thinking(current: str) -> str:
    console.print("[bold]Thinking mode[/]")
    for index, value in enumerate(THINKING_OPTIONS, start=1):
        current_marker = " (current)" if value == current else ""
        console.print(f"  [cyan]{index}.[/] {value}{current_marker}")
    while True:
        default_choice = (
            str(THINKING_OPTIONS.index(current) + 1)
            if current in THINKING_OPTIONS
            else "1"
        )
        raw = console.input(f"Select thinking mode [{default_choice}]: ").strip()
        choice = default_choice if not raw else raw
        if choice.isdigit():
            index = int(choice)
            if 1 <= index <= len(THINKING_OPTIONS):
                return THINKING_OPTIONS[index - 1]
        console.print(
            f"[yellow]Enter a number between 1 and {len(THINKING_OPTIONS)}.[/]"
        )


def prompt_allowed_chats(current: list[str]) -> list[str]:
    current_text = ", ".join(current) if current else "<none>"
    raw = console.input(
        f"[bold]Allowed chats[/] [{current_text}] (comma-separated, blank keeps current, '-' clears): "
    ).strip()
    if not raw:
        return current
    if raw == "-":
        return []
    return sorted(
        {
            normalize_chat(item)
            for item in [part.strip() for part in raw.split(",")]
            if item
        }
    )


def configure_app(config: Config) -> None:
    data = merge_config(load_toml(config.config_file))
    openai = data["openai"]
    bot = data["bot"]
    console.print(f"[bold]Config file:[/] [cyan]{config.config_file}[/]")
    console.print(
        "Press Enter to keep the current value. Enter '-' to clear text fields."
    )
    updated = merge_config(
        {
            "openai": {
                "api_key": prompt_text(
                    "OpenAI API key",
                    str(openai.get("api_key") or ""),
                    secret=True,
                ),
                "model": prompt_model(str(openai.get("model") or MODEL_OPTIONS[0])),
                "thinking": prompt_thinking(
                    str(openai.get("thinking") or DEFAULT_THINKING)
                ),
                "fast": prompt_bool("OpenAI fast mode", bool(openai.get("fast"))),
            },
            "bot": {
                "allow_groups": prompt_bool(
                    "Allow WhatsApp groups",
                    bool(bot.get("allow_groups")),
                ),
                "allowed_chats": prompt_allowed_chats(
                    list(bot.get("allowed_chats") or []),
                ),
                "system_prompt": prompt_text(
                    "System prompt",
                    str(bot.get("system_prompt") or ""),
                ),
            },
        }
    )
    config.config_file.write_text(render_config(updated), encoding="utf-8")
    console.print(f"[green]Saved[/] [cyan]{config.config_file}[/]")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="faltoobot")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("auth", help="authenticate the WhatsApp session")
    sub.add_parser("configure", help="create or update the config file interactively")
    sub.add_parser("run", help="run the WhatsApp bot in the foreground")
    sub.add_parser("install", help="install the background service")
    sub.add_parser("uninstall", help="remove the background service")
    sub.add_parser("status", help="show background service status")

    logs = sub.add_parser("logs", help="show Faltoobot logs")
    logs.add_argument(
        "-f", "--follow", action="store_true", help="follow the log output"
    )
    logs.add_argument(
        "-n", "--lines", type=int, default=100, help="number of lines to show"
    )

    update = sub.add_parser("update", help="pull the latest code and run migrations")
    update.add_argument("--migrate-only", action="store_true", help=argparse.SUPPRESS)

    sub.add_parser("paths", help="show important file paths")
    return parser.parse_args()


def show_paths(config: Config) -> None:
    table = Table(box=None, show_header=False, pad_edge=False)
    table.add_column(style="cyan", no_wrap=True)
    table.add_column()
    table.add_row("home", str(config.root))
    table.add_row("config", str(config.config_file))
    table.add_row("session_db", str(config.session_db))
    table.add_row("sessions", str(config.sessions_dir))
    table.add_row("log", str(config.log_file))
    table.add_row("service", str(service_file(config)))
    console.print(table)


def handle_async_command(args: argparse.Namespace, config: Config) -> bool:
    command = args.command
    if command == "auth":
        asyncio.run(run_auth(config))
        return True
    if command == "run":
        asyncio.run(run_bot(config))
        return True
    return False


def handle_command(args: argparse.Namespace, config: Config) -> None:
    if handle_async_command(args, config):
        return
    actions = {
        "configure": lambda: (ensure_config_file(), configure_app(config)),
        "install": lambda: install_service(config),
        "logs": lambda: tail_file(
            config.log_file, lines=args.lines, follow=args.follow
        ),
        "paths": lambda: (ensure_config_file(), show_paths(config)),
        "status": lambda: service_status(config),
        "uninstall": lambda: uninstall_service(config),
        "update": lambda: update_app(config, migrate_only=args.migrate_only),
    }
    action = actions.get(args.command)
    if action is None:  # guard for unexpected parser changes
        raise SystemExit(f"unknown command: {args.command}")
    action()


def main() -> None:
    args = parse_args()
    handle_command(args, build_config())
