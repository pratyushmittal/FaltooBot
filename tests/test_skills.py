from pathlib import Path
from typing import Any, cast

from faltoobot.gpt_utils import get_tools_definition
from faltoobot import skills


def _write_skill(root: Path, name: str, text: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")


def test_load_skills_prefers_workspace_skill(monkeypatch, tmp_path: Path) -> None:
    home_root = tmp_path / ".faltoobot"
    monkeypatch.setattr(skills, "app_root", lambda: home_root)
    workspace = tmp_path / "workspace"
    _write_skill(
        home_root / "skills",
        "pytest-helper",
        "---\nname: Pytest Helper\ndescription: home version\nkeywords: pytest, tests\n---\nUse home rules.\n",
    )
    _write_skill(
        workspace / ".faltoobot" / "skills",
        "pytest-helper",
        "---\nname: Pytest Helper\ndescription: workspace version\nkeywords:\n- pytest\n- e2e\n---\nUse workspace rules.\n",
    )

    loaded = skills.load_skills(workspace)

    assert len(loaded) == 1
    assert loaded[0]["source"] == "workspace"
    assert loaded[0]["description"] == "workspace version"
    assert loaded[0]["keywords"] == ("pytest", "e2e")
    assert loaded[0]["content"] == "Use workspace rules."


def test_search_skills_returns_best_match_with_content(
    monkeypatch, tmp_path: Path
) -> None:
    home_root = tmp_path / ".faltoobot"
    monkeypatch.setattr(skills, "app_root", lambda: home_root)
    workspace = tmp_path / "workspace"
    _write_skill(
        home_root / "skills",
        "pytest-helper",
        "---\nname: Pytest Helper\ndescription: Write small pytest e2e checks\nkeywords: pytest, tests, e2e\n---\nAlways keep tests small and prefer e2e coverage.\n",
    )
    _write_skill(
        home_root / "skills",
        "sql-helper",
        "---\nname: SQL Helper\ndescription: Query sqlite data\nkeywords: sqlite, sql\n---\nUse sqlite3 for quick inspection.\n",
    )

    result = skills.search_skills(workspace, "need pytest e2e test help")

    assert "Matched 1 local skill(s)" in result
    assert "## Pytest Helper" in result
    assert "keywords: pytest, tests, e2e" in result
    assert "Always keep tests small and prefer e2e coverage." in result
    assert str(home_root / "skills" / "pytest-helper") in result
    assert "SQL Helper" not in result


def test_get_search_skills_tool_builds_valid_tool_definition(
    monkeypatch, tmp_path: Path
) -> None:
    home_root = tmp_path / ".faltoobot"
    monkeypatch.setattr(skills, "app_root", lambda: home_root)

    tool = skills.get_search_skills_tool(tmp_path / "workspace")
    definition = get_tools_definition(tool)

    description = cast(str, definition["description"])
    parameters = cast(dict[str, Any], definition["parameters"])

    assert definition["type"] == "function"
    assert definition["name"] == "search_skills"
    assert definition["strict"] is True
    assert description.startswith(
        "Search local skill bundles and return the best matches."
    )
    assert "Project-local skills override home-level skills" in description
    assert parameters == {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language query describing the workflow, repo knowledge, or task help you need.",
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    }
