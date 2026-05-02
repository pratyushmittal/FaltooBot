from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from faltoobot import skills
from faltoobot.gpt_utils import get_tools_definition


@pytest.fixture(autouse=True)
def _isolate_bundled_skills(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        skills, "_bundled_skills_root", lambda: tmp_path / ".bundled-skills"
    )


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

    loaded = skills.load_skills(workspace, chat_key="code@test")

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

    assert skills.load_skills(workspace, chat_key="code@test") == []
    assert "frontmatter name does not match filename" in capsys.readouterr().err


def test_load_skills_reads_bundled_package_skills_when_app_root_is_empty(
    monkeypatch, tmp_path: Path
) -> None:
    home_root = tmp_path / ".faltoobot"
    monkeypatch.setattr(skills, "app_root", lambda: home_root)
    monkeypatch.setattr(skills.Path, "home", lambda: tmp_path)
    workspace = tmp_path / "workspace"

    package_root = tmp_path / "package" / "faltoobot"
    bundled = package_root / "skills"
    bundled.mkdir(parents=True, exist_ok=True)
    (bundled / "notification-listener.md").write_text(
        "---\ndescription: bundled helper\n---\nUse bundled rules.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(skills, "_bundled_skills_root", lambda: bundled)

    loaded = skills.load_skills(workspace, chat_key="code@test")

    assert loaded == [
        {
            "name": "notification-listener",
            "description": "bundled helper",
            "content": "Use bundled rules.",
        }
    ]


def test_load_skill_injects_runtime_placeholders(monkeypatch, tmp_path: Path) -> None:
    home_root = tmp_path / ".faltoobot"
    monkeypatch.setattr(skills, "app_root", lambda: home_root)
    monkeypatch.setattr(skills.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        skills,
        "build_config",
        lambda: SimpleNamespace(
            root=home_root,
            browser_binary="/tmp/chromium",
            document_pandoc_binary="/usr/bin/pandoc",
            document_mutool_binary="/usr/bin/mutool",
        ),
    )
    workspace = tmp_path / "workspace"
    _write_file_skill(
        home_root / "skills",
        "notification-listener",
        "---\ndescription: sub-agent helper\n---\nnotify key: {chat_key}\nbrowser: {browser_binary}\npandoc: {document_pandoc_binary}\nmutool: {document_mutool_binary}\nprofile: {browser_profile}\ncdp: {cdp_url}\nport: {cdp_port}\n",
    )

    result = skills.load_skill(workspace, "notification-listener", chat_key="code@main")

    assert result == "\n".join(
        [
            "notify key: code@main",
            "browser: /tmp/chromium",
            "pandoc: /usr/bin/pandoc",
            "mutool: /usr/bin/mutool",
            f"profile: {home_root / 'faltoobot'}",
            "cdp: http://127.0.0.1:9222",
            "port: 9222",
        ]
    )


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

    result = skills.load_skill(workspace, "pytest-helper", chat_key="code@test")

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

    result = skills.load_skill(workspace, "missing", chat_key="code@test")

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

    loaded, tool = skills.get_load_skill_tool(
        tmp_path / "workspace", chat_key="code@test"
    )
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
    assert description.startswith(
        "The following skills provide specialized instructions for specific tasks."
    )
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


def test_get_load_skill_tool_lists_skills_in_stable_name_order(
    monkeypatch, tmp_path: Path
) -> None:
    home_root = tmp_path / ".faltoobot"
    monkeypatch.setattr(skills, "app_root", lambda: home_root)
    monkeypatch.setattr(skills.Path, "home", lambda: tmp_path)
    _write_file_skill(
        home_root / "skills",
        "zeta-helper",
        "---\ndescription: last\n---\nZeta.\n",
    )
    _write_file_skill(
        home_root / "skills",
        "alpha-helper",
        "---\ndescription: first\n---\nAlpha.\n",
    )

    _loaded, tool = skills.get_load_skill_tool(
        tmp_path / "workspace", chat_key="code@test"
    )

    assert tool.__doc__ is not None
    assert tool.__doc__.index("- alpha-helper: first") < tool.__doc__.index(
        "- zeta-helper: last"
    )


def test_get_load_skill_tool_hides_subagent_disallowed_skills(
    monkeypatch, tmp_path: Path
) -> None:
    home_root = tmp_path / ".faltoobot"
    monkeypatch.setattr(skills, "app_root", lambda: home_root)
    monkeypatch.setattr(skills.Path, "home", lambda: tmp_path)
    workspace = tmp_path / "workspace"
    _write_file_skill(
        home_root / "skills",
        "notification-listener",
        "---\ndescription: async notify helper\nmeta: disallow-sub-agent\n---\nDo not load in sub-agents.\n",
    )
    _write_file_skill(
        home_root / "skills",
        "pytest-helper",
        "---\ndescription: allowed helper\n---\nStill available.\n",
    )

    loaded, tool = skills.get_load_skill_tool(
        workspace,
        chat_key="sub-agent@demo",
    )

    assert loaded == [
        {
            "name": "pytest-helper",
            "description": "allowed helper",
            "content": "Still available.",
        }
    ]
    assert tool("notification-listener") == (
        "Local skill not found: 'notification-listener'.\n\nAvailable skills:\n"
        "- pytest-helper: allowed helper"
    )


def test_bundled_browser_skill_uses_cdp_not_persistent_context() -> None:
    text = (Path(__file__).parents[1] / "faltoobot/skills/browser-use.md").read_text(
        encoding="utf-8"
    )
    example = text.split("```bash", 1)[1].split("```", 1)[0]

    assert "connect_over_cdp" in example
    assert '[faltoobot, "browser"]' in example
    assert '[faltoobot, "browser", url]' not in example
    assert "launch_persistent_context(" not in example
    assert "browser.new_context(" not in example
    assert "headless=" not in example
    assert "{browser_binary}" not in text
    assert "{browser_profile}" not in text
    assert "Prefer `run_shell_call`" in text
    assert "headless browser against the shared profile" in text
    assert "browser.new_context()" in text
    assert "Connected browser has no reusable login context" in example


def test_bundled_skills_do_not_reference_removed_python_shell_tool() -> None:
    skills_dir = Path(__file__).parents[1] / "faltoobot/skills"
    text = "\n".join(
        path.read_text(encoding="utf-8") for path in skills_dir.glob("*.md")
    )

    assert "run_in_python_shell" not in text
    assert "uv run --with" in text
