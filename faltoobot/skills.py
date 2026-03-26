import ast
import re
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict

from faltoobot.config import app_root

MAX_MATCHES = 3
MAX_SKILL_CHARS = 8_000
MIN_MATCH_SCORE = 4
LOW_CONFIDENCE_TOP_SCORE = 8
_SKILL_FILE_NAME = "skill.md"
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_./+-]*")


class Skill(TypedDict):
    key: str
    name: str
    description: str
    keywords: tuple[str, ...]
    path: str
    source: str
    content: str


def _parse_keywords(value: str) -> tuple[str, ...]:
    value = value.strip()
    if not value:
        return ()
    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            parsed = None
        if isinstance(parsed, list):
            return tuple(
                item.strip()
                for item in parsed
                if isinstance(item, str) and item.strip()
            )
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    lines = text.lstrip("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()

    fields: dict[str, str] = {}
    current_key: str | None = None
    for index, line in enumerate(lines[1:], start=1):
        stripped = line.strip()
        if stripped == "---":
            return fields, "\n".join(lines[index + 1 :]).strip()
        if current_key == "keywords" and stripped.startswith("- "):
            existing = fields.get("keywords", "")
            value = stripped.removeprefix("- ").strip()
            fields["keywords"] = f"{existing}, {value}" if existing else value
            continue
        if ":" not in line:
            current_key = None
            continue
        key, value = line.split(":", maxsplit=1)
        current_key = key.strip().lower()
        fields[current_key] = value.strip()
    return {}, text.strip()


def _read_skill(skill_dir: Path, source: str) -> Skill | None:
    if not skill_dir.is_dir():
        return None

    skill_files = [path for path in skill_dir.iterdir() if path.is_file()]
    skill_files = [
        path for path in skill_files if path.name.lower() == _SKILL_FILE_NAME
    ]
    if len(skill_files) != 1:
        return None

    text = skill_files[0].read_text(encoding="utf-8").strip()
    if not text:
        return None

    frontmatter, body = _split_frontmatter(text)
    content = (body or text).strip()
    if len(content) > MAX_SKILL_CHARS:
        content = content[: MAX_SKILL_CHARS - 14].rstrip() + "\n\n[truncated]"

    description = frontmatter.get("description", "")
    if not description:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped:
                description = (
                    stripped.lstrip("#").strip()
                    if stripped.startswith("#")
                    else stripped
                )
                break

    return {
        "key": skill_dir.name.strip().lower(),
        "name": frontmatter.get("name") or skill_dir.name,
        "description": description,
        "keywords": _parse_keywords(frontmatter.get("keywords", "")),
        "path": str(skill_dir.resolve()),
        "source": source,
        "content": content,
    }


def load_skills(workspace: Path) -> list[Skill]:
    roots = (
        (app_root() / "skills", "home"),
        (workspace.expanduser().resolve() / ".faltoobot" / "skills", "workspace"),
    )
    skills_by_key: dict[str, Skill] = {}
    for root, source in roots:
        if not root.is_dir():
            continue
        for skill_dir in sorted(root.iterdir()):
            if skill := _read_skill(skill_dir, source):
                skills_by_key[skill["key"]] = skill
    return sorted(skills_by_key.values(), key=lambda skill: skill["name"].lower())


def _score_skill(skill: Skill, query: str) -> int:
    query = query.strip().lower()
    if not query:
        return 1

    terms = set(_TOKEN_RE.findall(query))
    if not terms:
        return 1

    name = skill["name"].lower()
    description = skill["description"].lower()
    content = skill["content"].lower()
    keywords = {keyword.lower() for keyword in skill["keywords"]}
    fields = ((name, 8), (description, 5), (content, 1))

    score = sum(
        weight
        for text, weight in ((name, 20), (description, 12), (content, 6))
        if query in text
    )
    for term in terms:
        if term in keywords:
            score += 7
        score += sum(weight for text, weight in fields if term in text)
    return score


def search_skills(workspace: Path, query: str) -> str:
    skills = load_skills(workspace)
    ranked = sorted(
        ((skill, _score_skill(skill, query)) for skill in skills),
        key=lambda row: (-row[1], row[0]["name"].lower()),
    )
    positive = [(skill, score) for skill, score in ranked if score > 0]
    if not positive:
        available = "\n".join(
            f"- {skill['name']}: {skill['description'] or 'no description'}"
            for skill in skills
        )
        return f"No local skills matched: {query!r}.\n\nAvailable skills:\n{available or '(none)'}"

    top_score = positive[0][1]
    minimum_score = (
        1
        if top_score < LOW_CONFIDENCE_TOP_SCORE
        else max(MIN_MATCH_SCORE, top_score // 4)
    )
    matches = [skill for skill, score in positive if score >= minimum_score][
        :MAX_MATCHES
    ]

    blocks = [f"Matched {len(matches)} local skill(s) for: {query!r}."]
    for skill in matches:
        blocks.append(
            "\n".join(
                [
                    f"## {skill['name']}",
                    f"source: {skill['source']}",
                    f"path: {skill['path']}",
                    f"description: {skill['description'] or '(none)'}",
                    f"keywords: {', '.join(skill['keywords']) or '(none)'}",
                    "content:",
                    skill["content"],
                ]
            )
        )
    return "\n\n".join(blocks)


def get_search_skills_tool(workspace: Path) -> Callable[[str], str]:
    workspace = workspace.expanduser().resolve()

    def search_skills_tool(query: str) -> str:
        return search_skills(workspace, query)

    search_skills_tool.__name__ = "search_skills"
    search_skills_tool.__doc__ = f"""Search local skill bundles and return the best matches.

    Skills are loaded from `{app_root() / "skills"}` and `{workspace / ".faltoobot" / "skills"}`.
    Project-local skills override home-level skills with the same folder name.

    Args:
        - query: Natural-language query describing the workflow, repo knowledge, or task help you need.
    """
    return search_skills_tool
