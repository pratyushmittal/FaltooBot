import sys
from collections.abc import Callable
from pathlib import Path
from typing import NotRequired, TypedDict

from faltoobot.config import app_root, build_config
from faltoobot.cli import browser as browser_runtime


class Skill(TypedDict):
    name: str
    description: str
    content: str
    meta: NotRequired[list[str]]


def _bundled_skills_root() -> Path:
    return Path(__file__).resolve().parent / "skills"


def _skill_roots(workspace: Path) -> tuple[Path, Path, Path, Path]:
    return (
        _bundled_skills_root(),
        app_root() / "skills",
        Path.home() / ".agents" / "skills",
        workspace.expanduser().resolve() / ".skills",
    )


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    lines = text.lstrip("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()

    fields: dict[str, str] = {}
    for index, line in enumerate(lines[1:], start=1):
        stripped = line.strip()
        if stripped == "---":
            return fields, "\n".join(lines[index + 1 :]).strip()
        if ":" not in line:
            continue
        key, value = line.split(":", maxsplit=1)
        fields[key.strip().lower()] = value.strip()
    return {}, text.strip()


def _skill_key(name: str) -> str:
    return name.strip().lower()


def _skill_error(path: Path, message: str) -> None:
    print(f"skill error: {path}: {message}", file=sys.stderr)


def _parse_meta(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _read_skill_file(path: Path, *, default_name: str | None) -> Skill | None:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None

    frontmatter, body = _split_frontmatter(text)
    name = frontmatter.get("name", "").strip()
    if default_name is None:
        if not name:
            _skill_error(path, "SKILL.md must define a name in frontmatter")
            return None
    elif name and _skill_key(name) != _skill_key(default_name):
        _skill_error(path, "frontmatter name does not match filename")
        return None

    content = (body or text).strip()
    description = frontmatter.get("description", "").strip()
    skill: Skill = {
        "name": name or (default_name or ""),
        "description": description,
        "content": content,
    }
    meta = _parse_meta(frontmatter.get("meta", ""))
    if meta:
        skill["meta"] = meta
    return skill


def _iter_skill_files(root: Path) -> list[tuple[str, Path, str | None]]:
    if not root.is_dir():
        return []

    skill_files: list[tuple[str, Path, str | None]] = []
    for child in sorted(root.iterdir()):
        if child.is_file() and child.suffix.lower() == ".md":
            skill_files.append((child.name.lower(), child, child.stem))
        elif child.is_dir():
            path = child / "SKILL.md"
            if path.is_file():
                skill_files.append((child.name.lower(), path, None))
    return skill_files


def _filter_skills_for_chat_key(skills: list[Skill], chat_key: str) -> list[Skill]:
    if not chat_key.startswith("sub-agent@"):
        return skills
    return [
        skill for skill in skills if "disallow-sub-agent" not in skill.get("meta", [])
    ]


def load_skills(workspace: Path, *, chat_key: str) -> list[Skill]:
    skills_by_name: dict[str, Skill] = {}
    for root in _skill_roots(workspace):
        for _, path, default_name in _iter_skill_files(root):
            skill = _read_skill_file(path, default_name=default_name)
            if skill is None:
                continue
            skills_by_name[_skill_key(skill["name"])] = skill
    skills = sorted(skills_by_name.values(), key=lambda skill: skill["name"].lower())
    return _filter_skills_for_chat_key(skills, chat_key)


def _available_skills_text(skills: list[Skill]) -> str:
    ordered = sorted(skills, key=lambda skill: skill["name"].lower())
    return "\n".join(
        f"- {skill['name']}: {skill['description'] or '(no description)'}"
        for skill in ordered
    )


def _skill_context(chat_key: str) -> dict[str, str]:
    config = build_config()
    return {
        "chat_key": chat_key,
        "browser_binary": config.browser_binary,
        "document_pandoc_binary": config.document_pandoc_binary or "pandoc",
        "document_mutool_binary": config.document_mutool_binary or "mutool",
        "browser_profile": str(browser_runtime.browser_profile_dir(config.root)),
        "cdp_url": f"http://127.0.0.1:{browser_runtime.CDP_PORT}",
        "cdp_port": str(browser_runtime.CDP_PORT),
    }


def load_skill(workspace: Path, skill_name: str, *, chat_key: str) -> str:
    skills = load_skills(workspace, chat_key=chat_key)
    by_name = {_skill_key(skill["name"]): skill for skill in skills}
    skill = by_name.get(_skill_key(skill_name))
    if skill is not None:
        content = skill["content"]
        for key, value in _skill_context(chat_key).items():
            content = content.replace(f"{{{key}}}", value)
        return content

    available = _available_skills_text(skills)
    return (
        f"Local skill not found: {skill_name!r}.\n\nAvailable skills:\n"
        f"{available or '(none)'}"
    )


def get_load_skill_tool(
    workspace: Path, *, chat_key: str
) -> tuple[list[Skill], Callable[[str], str]]:
    workspace = workspace.expanduser().resolve()
    skills = load_skills(workspace, chat_key=chat_key)
    available = _available_skills_text(skills)

    def load_skill_tool(skill_name: str) -> str:
        return load_skill(workspace, skill_name, chat_key=chat_key)

    load_skill_tool.__name__ = "load_skill"
    load_skill_tool.__doc__ = f"""The following skills provide specialized instructions for specific tasks.
When a task matches a skill's description, use this `{load_skill_tool.__name__}` tool before proceeding.

Available skills:
{available or "- (none)"}

Args:
    - skill_name: Exact local skill name to load.
"""
    return skills, load_skill_tool
