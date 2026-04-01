from pathlib import Path

from faltoobot.config import Config
from faltoobot.prompts.coding_agent import PROMPT as CODING_AGENT_PROMPT
from faltoobot.prompts.whatsapp import PROMPT as WHATSAPP_PROMPT


def _agents_file(path: Path) -> Path:
    return path / "AGENTS.md"


def _read_agents_text(path: Path) -> str | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def _instruction_parts(prompt: str, config: Config, workspace: Path) -> list[str]:
    parts = [prompt]
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


def get_system_instructions(config: Config, chat_key: str, workspace: Path) -> str:
    match chat_key:
        case key if key.startswith("code@"):
            prompt = CODING_AGENT_PROMPT
        case _:
            prompt = WHATSAPP_PROMPT
    return "\n\n".join(
        part for part in _instruction_parts(prompt, config, workspace) if part
    )
