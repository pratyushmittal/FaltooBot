from __future__ import annotations

import json
import re
import runpy
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from faltoobot.config import Config

VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
STATE_FILE = "migration-state.json"


def migration_state_file(config: Config) -> Path:
    return config.root / STATE_FILE


def parse_version(name: str) -> tuple[int, int, int] | None:
    match = VERSION_RE.fullmatch(name)
    if not match:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def migration_scripts(root: Path) -> list[tuple[str, Path]]:
    path = root / "migrations"
    if not path.is_dir():
        return []
    scripts: list[tuple[str, Path]] = []
    for child in path.iterdir():
        version = parse_version(child.name)
        if version is None:
            continue
        script = child / "migrate.py"
        if script.is_file():
            scripts.append((child.name, script))
    scripts.sort(key=lambda item: parse_version(item[0]) or (0, 0, 0))
    return scripts


def read_applied_versions(config: Config) -> list[str]:
    path = migration_state_file(config)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    versions = payload.get("applied_versions")
    if not isinstance(versions, list):
        return []
    return [version for version in versions if isinstance(version, str)]


def write_applied_versions(config: Config, versions: list[str]) -> None:
    path = migration_state_file(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"applied_versions": versions}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run_release_migrations(config: Config, root: Path) -> list[str]:
    applied_versions = read_applied_versions(config)
    applied = set(applied_versions)
    ran: list[str] = []
    for version, script in migration_scripts(root):
        if version in applied:
            continue
        namespace: dict[str, Any] = runpy.run_path(str(script))
        migrate = namespace.get("migrate")
        if not callable(migrate):
            raise SystemExit(f"Migration {version} is missing migrate(config).")
        migrate(config)
        applied_versions.append(version)
        applied.add(version)
        write_applied_versions(config, applied_versions)
        ran.append(version)
    return ran
