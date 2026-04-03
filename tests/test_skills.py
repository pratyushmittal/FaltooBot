from pathlib import Path
from typing import Any, cast

from faltoobot import skills
from faltoobot.gpt_utils import get_tools_definition


def _write_folder_skill(root: Path, name: str, text: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")


def _write_file_skill(root: Path, name: str, text: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.md").write_text(text, encoding="utf-8")


def test_load_skills_reads_all_roots_and_prefers_workspace(
    monkeypatch, tmp_path: Path
) -> None:
    home_root = tmp_path / ".faltoobot"
    agents_root = tmp_path / ".agents"
    monkeypatch.setattr(skills, "app_root", lambda: home_root)
    monkeypatch.setattr(skills.Path, "home", lambda: tmp_path)
    workspace = tmp_path / "workspace"

    _write_file_skill(
        home_root / "skills",
        "pytest-helper",
        "---\ndescription: home version\n---\nUse home rules.\n",
    )
    _write_folder_skill(
        agents_root / "skills",
        "sql-helper",
        "---\nname: SQL Helper\ndescription: Query sqlite data\n---\nUse sqlite3 for quick inspection.\n",
    )
    _write_file_skill(
        workspace / ".skills",
        "pytest-helper",
        "---\ndescription: workspace version\n---\nUse workspace rules.\n",
    )

    loaded = skills.load_skills(workspace)

    assert loaded == [
        {
            "name": "pytest-helper",
            "description": "workspace version",
            "content": "Use workspace rules.",
        },
        {
            "name": "SQL Helper",
            "description": "Query sqlite data",
            "content": "Use sqlite3 for quick inspection.",
        },
    ]


def test_load_skills_skips_direct_file_with_conflicting_name(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    home_root = tmp_path / ".faltoobot"
    monkeypatch.setattr(skills, "app_root", lambda: home_root)
    monkeypatch.setattr(skills.Path, "home", lambda: tmp_path)
    workspace = tmp_path / "workspace"

    _write_file_skill(
        home_root / "skills",
        "pytest-helper",
        "---\nname: Totally Different\ndescription: mismatch\n---\nIgnored.\n",
    )

    assert skills.load_skills(workspace) == []
    assert "frontmatter name does not match filename" in capsys.readouterr().err


def test_load_skill_returns_exact_skill_content(monkeypatch, tmp_path: Path) -> None:
    home_root = tmp_path / ".faltoobot"
    monkeypatch.setattr(skills, "app_root", lambda: home_root)
    monkeypatch.setattr(skills.Path, "home", lambda: tmp_path)
    workspace = tmp_path / "workspace"
    _write_file_skill(
        home_root / "skills",
        "pytest-helper",
        "---\ndescription: Write small pytest e2e checks\n---\nAlways keep tests small and prefer e2e coverage.\n",
    )

    result = skills.load_skill(workspace, "pytest-helper")

    assert result == "Always keep tests small and prefer e2e coverage."


def test_load_skill_lists_available_skills_when_missing(
    monkeypatch, tmp_path: Path
) -> None:
    home_root = tmp_path / ".faltoobot"
    monkeypatch.setattr(skills, "app_root", lambda: home_root)
    monkeypatch.setattr(skills.Path, "home", lambda: tmp_path)
    workspace = tmp_path / "workspace"
    _write_file_skill(
        home_root / "skills",
        "pytest-helper",
        "---\ndescription: Write small pytest e2e checks\n---\nAlways keep tests small.\n",
    )

    result = skills.load_skill(workspace, "missing")

    assert "Local skill not found" in result
    assert "pytest-helper: Write small pytest e2e checks" in result


def test_get_load_skill_tool_builds_valid_tool_definition(
    monkeypatch, tmp_path: Path
) -> None:
    home_root = tmp_path / ".faltoobot"
    monkeypatch.setattr(skills, "app_root", lambda: home_root)
    monkeypatch.setattr(skills.Path, "home", lambda: tmp_path)
    _write_file_skill(
        home_root / "skills",
        "pytest-helper",
        "---\ndescription: Write small pytest e2e checks\n---\nAlways keep tests small.\n",
    )

    loaded, tool = skills.get_load_skill_tool(tmp_path / "workspace")
    definition = get_tools_definition(tool)

    assert loaded == [
        {
            "name": "pytest-helper",
            "description": "Write small pytest e2e checks",
            "content": "Always keep tests small.",
        }
    ]

    description = cast(str, definition["description"])
    parameters = cast(dict[str, Any], definition["parameters"])

    assert definition["type"] == "function"
    assert definition["name"] == "load_skill"
    assert definition["strict"] is True
    assert description.startswith("Load the contents of a local skill by name.")
    assert "pytest-helper: Write small pytest e2e checks" in description
    assert parameters == {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "Exact local skill name to load.",
            }
        },
        "required": ["skill_name"],
        "additionalProperties": False,
    }
