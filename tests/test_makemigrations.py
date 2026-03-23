import asyncio
from pathlib import Path
from typing import Any

from faltoobot.cli import migrations


def test_build_makemigrations_messages_includes_readme(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "README.md").write_text("hello migration guide", encoding="utf-8")

    messages = migrations.build_makemigrations_messages(tmp_path)

    assert messages[0]["role"] == "user"
    assert migrations.PROMPT in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "hello migration guide" in messages[1]["content"]


def test_run_makemigrations_passes_readme_to_streaming_reply(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "README.md").write_text("guide text", encoding="utf-8")
    calls: list[dict[str, Any]] = []

    async def fake_get_streaming_reply(
        instructions: str, input: list[dict[str, Any]], tools
    ):
        calls.append(
            {
                "instructions": instructions,
                "input": input,
                "tools": tools,
            }
        )
        yield type("Event", (), {"type": "response.output_text.delta", "delta": "ok"})()

    monkeypatch.setattr(migrations, "get_streaming_reply", fake_get_streaming_reply)
    monkeypatch.setattr(migrations, "get_run_shell_call_tool", lambda root: "tool")

    asyncio.run(migrations.run_makemigrations(tmp_path))

    assert calls[0]["instructions"] == migrations.INSTRUCTIONS
    assert calls[0]["tools"] == ["tool"]
    assert calls[0]["input"][0]["content"] == migrations.PROMPT
    assert "guide text" in calls[0]["input"][1]["content"]
    assert capsys.readouterr().out == "ok\n"
