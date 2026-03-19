import os
import re
import shlex
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

COMMANDS = (
    ("/help", "show help"),
    ("/tree", "open messages file"),
    ("/reset", "start a new session"),
    ("/exit", "exit chat"),
)
PROMPT_ARG_RE = re.compile(r"\$(\d+)\b")
PROMPT_RANGE_RE = re.compile(r"\$\{@:(?P<start>\d+)(?::(?P<end>\d+))?\}")
FRONTMATTER_PARTS = 2
QUOTED_TEXT_MIN_LENGTH = 2


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    command: str
    detail: str
    body: str


def prompts_dir(root: Path) -> Path:
    return root / "prompts"


def prompt_command_name(path: Path) -> str | None:
    name = path.stem.strip()
    if not name or any(char.isspace() for char in name):
        return None
    return f"/{name}"


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("\n---\n", 1)
    if len(parts) != FRONTMATTER_PARTS:
        return {}, text
    meta = {}
    for line in parts[0].splitlines()[1:]:
        key, sep, value = line.partition(":")
        if not sep:
            continue
        cleaned = value.strip()
        if (
            len(cleaned) >= QUOTED_TEXT_MIN_LENGTH
            and cleaned[0] == cleaned[-1]
            and cleaned[0] in {'"', "'"}
        ):
            cleaned = cleaned[1:-1]
        meta[key.strip()] = cleaned
    return meta, parts[1]


def prompt_detail(name: str, body: str, meta: dict[str, str]) -> str:
    if description := meta.get("description"):
        return description
    for line in body.splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned.lstrip("#").strip()
    return f"saved prompt {name}"


def prompt_templates(root: Path) -> tuple[PromptTemplate, ...]:
    directory = prompts_dir(root)
    if not directory.exists():
        return ()
    reserved = {command for command, _ in COMMANDS}
    templates: list[PromptTemplate] = []
    for path in sorted(directory.glob("*.md")):
        if not path.is_file():
            continue
        command = prompt_command_name(path)
        if command is None or command in reserved:
            continue
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        meta, body = split_frontmatter(text)
        body = body.strip()
        if not body:
            continue
        templates.append(
            PromptTemplate(command, prompt_detail(path.stem, body, meta), body)
        )
    return tuple(templates)


def slash_commands(root: Path) -> tuple[tuple[str, str], ...]:
    return COMMANDS + tuple(
        (template.command, template.detail) for template in prompt_templates(root)
    )


def split_saved_prompt(text: str) -> tuple[str, list[str]]:
    command, _, remainder = text.strip().partition(" ")
    args_text = remainder.strip()
    if not args_text:
        return command, []
    try:
        return command, shlex.split(args_text)
    except ValueError:
        return command, args_text.split()


def expand_prompt_body(body: str, args: list[str]) -> str:
    all_args = " ".join(args)

    def replace_range(match: re.Match[str]) -> str:
        start = max(0, int(match.group("start")) - 1)
        end_text = match.group("end")
        end = int(end_text) if end_text else len(args)
        return " ".join(args[start:end])

    expanded = body.replace("$ARGUMENTS", all_args).replace("$@", all_args)
    expanded = PROMPT_RANGE_RE.sub(replace_range, expanded)
    return PROMPT_ARG_RE.sub(
        lambda match: (
            args[index]
            if (index := int(match.group(1)) - 1) in range(len(args))
            else ""
        ),
        expanded,
    ).strip()


def expand_saved_prompt(root: Path, text: str) -> str | None:
    command, args = split_saved_prompt(text)
    for template in prompt_templates(root):
        if template.command == command:
            return expand_prompt_body(template.body, args)
    return None


def default_session_name() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def help_text() -> str:
    names = ", ".join(name for name, _ in COMMANDS)
    return f"Commands: {names}. Saved prompts: ~/.faltoobot/prompts/*.md. Ctrl+V image"


def slash_query(text: str) -> str | None:
    query = text.strip()
    if not query.startswith("/") or any(char.isspace() for char in query):
        return None
    return query


def slash_suggestions(
    text: str,
    commands: tuple[tuple[str, str], ...] = COMMANDS,
) -> tuple[tuple[str, str], ...]:
    query = slash_query(text)
    if query is None:
        return ()
    if query == "/":
        return commands
    return tuple(item for item in commands if item[0].startswith(query))


def completed_slash_query(
    text: str,
    suggestions: tuple[tuple[str, str], ...],
) -> str | None:
    query = slash_query(text)
    if query is None or not suggestions:
        return None
    names = [command for command, _ in suggestions]
    prefix = os.path.commonprefix(names)
    if prefix.startswith(query) and len(prefix) > len(query):
        return prefix
    return names[0]


def session_name(name: str | None) -> str:
    return f"CLI {name or default_session_name()}"
