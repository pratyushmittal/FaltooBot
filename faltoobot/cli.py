import argparse
import asyncio
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

from faltoobot.bot import run_auth, run_bot
from faltoobot.config import (
    APP_LABEL,
    Config,
    build_config,
    ensure_config_file,
    migrate_config_file,
)
from faltoobot.store import open_db


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
    db = await open_db(str(config.state_db))
    await db.close()
    changes.append("state_db")
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="faltoobot")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("auth", help="authenticate the WhatsApp session")
    sub.add_parser("run", help="run the WhatsApp bot in the foreground")
    sub.add_parser("install", help="install the macOS launchd service")
    sub.add_parser("uninstall", help="remove the macOS launchd service")
    sub.add_parser("status", help="show launchd status")

    logs = sub.add_parser("logs", help="show Faltoobot logs")
    logs.add_argument("-f", "--follow", action="store_true", help="follow the log output")
    logs.add_argument("-n", "--lines", type=int, default=100, help="number of lines to show")

    update = sub.add_parser("update", help="pull the latest code and run migrations")
    update.add_argument("--migrate-only", action="store_true", help=argparse.SUPPRESS)

    paths = sub.add_parser("paths", help="show important file paths")
    paths.add_argument("--config", action="store_true", help="only print the config file")
    return parser.parse_args()


def show_paths(config: Config, config_only: bool) -> None:
    if config_only:
        print(config.config_file)
        return
    print(f"home: {config.root}")
    print(f"config: {config.config_file}")
    print(f"session_db: {config.session_db}")
    print(f"state_db: {config.state_db}")
    print(f"log: {config.log_file}")
    print(f"launch_agent: {config.launch_agent}")


def main() -> None:
    args = parse_args()
    config = build_config()
    if args.command == "auth":
        asyncio.run(run_auth(config))
        return
    if args.command == "run":
        asyncio.run(run_bot(config))
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
        show_paths(config, config_only=args.config)
        return
