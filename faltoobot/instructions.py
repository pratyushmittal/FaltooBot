from pathlib import Path

from faltoobot.config import Config


def _agents_file(path: Path) -> Path:
    return path / "AGENTS.md"


def _read_agents_text(path: Path) -> str | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def _instruction_parts(config: Config, workspace: Path) -> list[str]:
    parts = [config.system_prompt]
    seen = set[Path]()
    for base, label in (
        (config.root, "Global AGENTS.md"),
        (Path.home(), "Home AGENTS.md"),
        (workspace, "Session AGENTS.md"),
    ):
        agents_path = _agents_file(base).resolve()
        if agents_path in seen:
            continue
        seen.add(agents_path)
        if text := _read_agents_text(agents_path):
            parts.append(f"{label}:\n{text}")
    return parts


def system_instructions(config: Config, workspace: Path) -> str:
    return "\n\n".join(part for part in _instruction_parts(config, workspace) if part)
