import json
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from urllib.request import urlopen
from faltoobot.config import app_root
from faltoobot.migrate import _version_tuple

_UPDATE_FILE = "last_update.json"
_PYPI_URL = "https://pypi.org/pypi/faltoobot/json"


def _latest_package_version() -> str | None:
    try:
        with urlopen(_PYPI_URL, timeout=2) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, json.JSONDecodeError):
        return None

    info = data.get("info") if isinstance(data, dict) else None
    version = info.get("version") if isinstance(info, dict) else None
    return version if isinstance(version, str) else None


def available_update_notice(current_version: str) -> str:
    latest_version = _latest_package_version()
    if not latest_version or _version_tuple(latest_version) <= _version_tuple(
        current_version
    ):
        return ""
    return (
        f"New Faltoobot version available: {latest_version} "
        f"(current {current_version}). Run `faltoobot update` to upgrade."
    )


def _changelog_path() -> Path | None:
    repo_path = Path(__file__).resolve().parents[1] / "CHANGELOG.md"
    if repo_path.exists():
        return repo_path

    try:
        dist = distribution("faltoobot")
    except PackageNotFoundError:
        return None
    for file in dist.files or []:
        if str(file).endswith("share/faltoobot/CHANGELOG.md"):
            path = Path(str(dist.locate_file(file)))
            return path if path.exists() else None
    return None


def _section_version(line: str) -> str | None:
    if not line.startswith("## "):
        return None
    value = line.removeprefix("## ").split("—", maxsplit=1)[0].strip()
    return value or None


def changelog_between(previous_version: str, current_version: str) -> str:
    path = _changelog_path()
    if path is None:
        return f"Updated Faltoobot from {previous_version} to {current_version}."

    previous = _version_tuple(previous_version)
    current = _version_tuple(current_version)
    sections: list[str] = []
    keep = False
    current_section: list[str] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        if version := _section_version(line):
            if keep and current_section:
                sections.append("\n".join(current_section).strip())
            current_section = [line]
            section_version = _version_tuple(version)
            keep = previous < section_version <= current
            continue
        if keep:
            current_section.append(line)

    if keep and current_section:
        sections.append("\n".join(current_section).strip())

    if not sections:
        return f"Updated Faltoobot from {previous_version} to {current_version}."
    return "\n\n".join(sections)


def record_update(previous_version: str, current_version: str) -> None:
    if previous_version == current_version:
        return
    path = app_root() / _UPDATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "previous_version": previous_version,
                "current_version": current_version,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def consume_changelog_update() -> str:
    path = app_root() / _UPDATE_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return ""
    finally:
        path.unlink(missing_ok=True)

    previous = data.get("previous_version") if isinstance(data, dict) else None
    current = data.get("current_version") if isinstance(data, dict) else None
    if not isinstance(previous, str) or not isinstance(current, str):
        return ""
    return changelog_between(previous, current)
