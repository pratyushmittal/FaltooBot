from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path

from faltoobot.config import app_root

PREVIEW_TEXT_LIMIT = 48


@dataclass(slots=True)
class SlashCommand:
    name: str
    path: Path
    template: str
    preview: str


@dataclass(slots=True)
class SlashCommandStore:
    """Cache slash commands and only re-read prompt files after metadata changes."""

    excluded_commands: frozenset[str] = field(default_factory=frozenset)
    prompts_dir: Path | None = None
    _signature: tuple[tuple[str, int, int], ...] = field(
        default_factory=tuple,
        init=False,
        repr=False,
    )
    _commands: dict[str, SlashCommand] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    def commands(self) -> dict[str, SlashCommand]:
        self.refresh()
        return dict(self._commands)

    def refresh(self) -> None:
        prompts_dir = self.prompts_dir or app_root() / "prompts"
        signature = _prompt_signature(prompts_dir)
        if signature == self._signature:
            return
        self._signature = signature
        self._commands = _discover_slash_commands(prompts_dir, self.excluded_commands)


def _discover_slash_commands(
    prompts_dir: Path,
    excluded_commands: Collection[str],
) -> dict[str, SlashCommand]:
    if not prompts_dir.exists() or not prompts_dir.is_dir():
        return {}
    commands: dict[str, SlashCommand] = {}
    for path in sorted(prompts_dir.iterdir(), key=lambda item: item.name):
        if not path.is_file() or path.suffix != ".md":
            continue
        template = path.read_text(encoding="utf-8")
        prompt = SlashCommand(
            name=path.stem,
            path=path,
            template=template,
            preview=_preview_for_template(template),
        )
        command = f"/{prompt.name}"
        if command in excluded_commands:
            continue
        commands[command] = prompt
    return commands


def _prompt_signature(prompts_dir: Path) -> tuple[tuple[str, int, int], ...]:
    if not prompts_dir.exists() or not prompts_dir.is_dir():
        return ()
    signature: list[tuple[str, int, int]] = []
    for path in sorted(prompts_dir.iterdir(), key=lambda item: item.name):
        if not path.is_file() or path.suffix != ".md":
            continue
        stat = path.stat()
        signature.append((path.name, stat.st_mtime_ns, stat.st_size))
    return tuple(signature)


def _preview_for_template(template: str) -> str:
    for line in template.splitlines():
        stripped = line.strip()
        if stripped:
            if len(stripped) <= PREVIEW_TEXT_LIMIT:
                return stripped
            return f"{stripped[:PREVIEW_TEXT_LIMIT].rstrip()}..."
    return "slash command"
