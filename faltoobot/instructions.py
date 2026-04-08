from pathlib import Path

from faltoobot.config import Config
from faltoobot.memory import render_memory_prompt
from faltoobot.prompts.coding_agent import PROMPT as CODING_AGENT_PROMPT
from faltoobot.prompts.sub_agent import PROMPT as SUB_AGENT_PROMPT
from faltoobot.prompts.whatsapp import PROMPT as WHATSAPP_PROMPT


def _agents_file(path: Path) -> Path:
    return path / "AGENTS.md"


def _read_agents_text(path: Path) -> str | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def _instruction_parts(prompt: str, config: Config, workspace: Path) -> list[str]:
    parts = [prompt.format(bot_name=config.bot_name)]
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
        case key if key.startswith("sub-agent@"):
            prompt = SUB_AGENT_PROMPT
        case key if key.startswith("code@"):
            prompt = CODING_AGENT_PROMPT
        case _:
            prompt = WHATSAPP_PROMPT

    parts = _instruction_parts(prompt, config, workspace)
    if memory_text := render_memory_prompt(config.root, chat_key):
        parts.append(memory_text)
    return "\n\n".join(part for part in parts if part)
