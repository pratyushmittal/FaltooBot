import argparse
import asyncio
import getpass
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

from faltoobot.bot import run_auth, run_bot
from faltoobot.chat import run_chat
from faltoobot.config import (
    APP_LABEL,
    MODEL_OPTIONS,
    DEFAULT_THINKING,
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


def require_macos() -> None:
    if sys.platform != "darwin":
        raise SystemExit("This command currently supports macOS only.")


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def uid() -> str:
    return str(os.getuid())


def service_target() -> str:
    return f"gui/{uid()}/{APP_LABEL}"


def write_run_script(config: Config) -> None:
    project_dir = project_root()
    config.run_script.write_text(
        "\n".join(
            [
                "#!/bin/zsh",
                f"cd {project_dir.as_posix()!r}",
                f"exec {uv_bin()!r} run faltoobot run",
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
        "WorkingDirectory": str(project_root()),
        "StandardOutPath": str(config.log_file),
        "StandardErrorPath": str(config.log_file),
    }
    config.launch_agent.write_bytes(plistlib.dumps(data))


def run_launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["launchctl", *args], check=check, text=True, capture_output=True)


def run_cmd(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(list(args), check=True, text=True, cwd=cwd)


def read_cmd(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(list(args), check=True, text=True, cwd=cwd, capture_output=True)
    return result.stdout


def uv_bin() -> str:
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("uv is required. Install it first: https://docs.astral.sh/uv/")
    return uv


def has_service(config: Config) -> bool:
    return sys.platform == "darwin" and config.launch_agent.exists()


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
        print("Migrations:", ", ".join(changes))
        return

    repo = project_root()
    if not (repo / ".git").exists():
        raise SystemExit("`faltoobot update` only works from a git clone of the repo.")
    status = read_cmd("git", "status", "--short", cwd=repo).strip()
    if status:
        raise SystemExit("Commit or stash local changes before running `faltoobot update`.")
    run_cmd("git", "pull", "--ff-only", cwd=repo)
    run_cmd(uv_bin(), "sync", cwd=repo)
    run_cmd(uv_bin(), "run", "faltoobot", "update", "--migrate-only", cwd=repo)


def install_service(config: Config) -> None:
    require_macos()
    ensure_config_file()
    config.root.mkdir(parents=True, exist_ok=True)
    write_run_script(config)
    write_launch_agent(config)
    run_launchctl("bootout", f"gui/{uid()}", config.launch_agent.as_posix(), check=False)
    run_launchctl("bootstrap", f"gui/{uid()}", config.launch_agent.as_posix())
    run_launchctl("enable", service_target(), check=False)
    run_launchctl("kickstart", "-k", service_target())
    print(f"Installed {APP_LABEL}")
    print(f"config: {config.config_file}")
    print(f"logs: {config.log_file}")


def uninstall_service(config: Config) -> None:
    require_macos()
    run_launchctl("bootout", f"gui/{uid()}", config.launch_agent.as_posix(), check=False)
    if config.launch_agent.exists():
        config.launch_agent.unlink()
    if config.run_script.exists():
        config.run_script.unlink()
    print(f"Removed {APP_LABEL}")


def service_status(config: Config) -> None:
    require_macos()
    result = run_launchctl("print", service_target(), check=False)
    if result.returncode == 0:
        print(f"{APP_LABEL}: loaded")
        return
    print(f"{APP_LABEL}: not loaded")
    if config.launch_agent.exists():
        print(f"plist: {config.launch_agent}")


def tail_file(path: Path, lines: int = 100, follow: bool = False) -> None:
    if not path.exists():
        print(f"No log file at {path}")
        return
    data = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in data[-lines:]:
        print(line)
    if not follow:
        return
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(0, os.SEEK_END)
        while True:
            line = handle.readline()
            if line:
                print(line, end="")
                continue
            time.sleep(0.5)


def prompt_text(label: str, current: str, *, secret: bool = False) -> str:
    current_text = "[set]" if secret and current else f"[{current}]" if current else "[empty]"
    raw = (
        getpass.getpass(f"{label} {current_text} (blank keeps current): ")
        if secret
        else input(f"{label} {current_text} (blank keeps current): ")
    ).strip()
    if not raw:
        return current
    return "" if raw == "-" else raw


def prompt_bool(label: str, current: bool) -> bool:
    current_text = "y" if current else "n"
    while True:
        raw = input(f"{label} [y/n] (blank keeps {current_text}): ").strip().lower()
        if not raw:
            return current
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("Enter y or n.")


def prompt_model(current: str) -> str:
    print("OpenAI model:")
    for index, model in enumerate(MODEL_OPTIONS, start=1):
        current_marker = " (current)" if model == current else ""
        print(f"  {index}. {model}{current_marker}")
    custom_index = len(MODEL_OPTIONS) + 1
    custom_marker = " (current)" if current and current not in MODEL_OPTIONS else ""
    print(f"  {custom_index}. custom{custom_marker}")
    while True:
        default_choice = (
            str(MODEL_OPTIONS.index(current) + 1) if current in MODEL_OPTIONS else str(custom_index)
        )
        raw = input(f"Select model [{default_choice}]: ").strip()
        if not raw:
            choice = default_choice
        else:
            choice = raw
        if choice.isdigit():
            index = int(choice)
            if 1 <= index <= len(MODEL_OPTIONS):
                return MODEL_OPTIONS[index - 1]
            if index == custom_index:
                return prompt_text("Custom model", current if current not in MODEL_OPTIONS else "")
        print(f"Enter a number between 1 and {custom_index}.")


def prompt_thinking(current: str) -> str:
    print("Thinking mode:")
    for index, value in enumerate(THINKING_OPTIONS, start=1):
        current_marker = " (current)" if value == current else ""
        print(f"  {index}. {value}{current_marker}")
    while True:
        default_choice = str(THINKING_OPTIONS.index(current) + 1) if current in THINKING_OPTIONS else "1"
        raw = input(f"Select thinking mode [{default_choice}]: ").strip()
        choice = default_choice if not raw else raw
        if choice.isdigit():
            index = int(choice)
            if 1 <= index <= len(THINKING_OPTIONS):
                return THINKING_OPTIONS[index - 1]
        print(f"Enter a number between 1 and {len(THINKING_OPTIONS)}.")


def prompt_allowed_chats(current: list[str]) -> list[str]:
    current_text = ", ".join(current) if current else "<none>"
    raw = input(
        f"Allowed chats [{current_text}] (comma-separated, blank keeps current, '-' clears): "
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
    print(f"Config file: {config.config_file}")
    print("Press Enter to keep the current value. Enter '-' to clear text fields.")
    updated = merge_config(
        {
            "openai": {
                "api_key": prompt_text(
                    "OpenAI API key",
                    str(openai.get("api_key") or ""),
                    secret=True,
                ),
                "model": prompt_model(str(openai.get("model") or MODEL_OPTIONS[0])),
                "thinking": prompt_thinking(str(openai.get("thinking") or DEFAULT_THINKING)),
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
    print(f"Saved {config.config_file}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="faltoobot")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("auth", help="authenticate the WhatsApp session")
    sub.add_parser("configure", help="create or update the config file interactively")
    sub.add_parser("run", help="run the WhatsApp bot in the foreground")
    chat = sub.add_parser("chat", help="start a new CLI chat session")
    chat.add_argument("--name", help="optional session name")
    sub.add_parser("install", help="install the macOS launchd service")
    sub.add_parser("uninstall", help="remove the macOS launchd service")
    sub.add_parser("status", help="show launchd status")

    logs = sub.add_parser("logs", help="show Faltoobot logs")
    logs.add_argument("-f", "--follow", action="store_true", help="follow the log output")
    logs.add_argument("-n", "--lines", type=int, default=100, help="number of lines to show")

    update = sub.add_parser("update", help="pull the latest code and run migrations")
    update.add_argument("--migrate-only", action="store_true", help=argparse.SUPPRESS)

    sub.add_parser("paths", help="show important file paths")
    return parser.parse_args()


def show_paths(config: Config) -> None:
    print(f"home: {config.root}")
    print(f"config: {config.config_file}")
    print(f"session_db: {config.session_db}")
    print(f"sessions: {config.sessions_dir}")
    print(f"log: {config.log_file}")
    print(f"launch_agent: {config.launch_agent}")


def main() -> None:
    args = parse_args()
    config = build_config()
    if args.command == "auth":
        asyncio.run(run_auth(config))
        return
    if args.command == "configure":
        ensure_config_file()
        configure_app(config)
        return
    if args.command == "run":
        asyncio.run(run_bot(config))
        return
    if args.command == "chat":
        asyncio.run(run_chat(config, name=args.name))
        return
    if args.command == "update":
        update_app(config, migrate_only=args.migrate_only)
        return
    if args.command == "install":
        install_service(config)
        return
    if args.command == "uninstall":
        uninstall_service(config)
        return
    if args.command == "status":
        service_status(config)
        return
    if args.command == "logs":
        tail_file(config.log_file, lines=args.lines, follow=args.follow)
        return
    if args.command == "paths":
        ensure_config_file()
        show_paths(config)
        return
