from pathlib import Path

import pytest

from faltoobot.faltoochat import slash_commands
from faltoobot.faltoochat.slash_commands import SlashCommandStore


def make_prompt_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / ".faltoobot"
    prompts_dir = root / "prompts"
    prompts_dir.mkdir(parents=True)
    return root, prompts_dir


def test_slash_command_store_reads_flat_markdown_files(tmp_path: Path) -> None:
    _root, prompts_dir = make_prompt_root(tmp_path)
    (prompts_dir / "fix-tests.md").write_text(
        "\n\nInvestigate and fix {target}.\nExplain the root cause briefly.\n",
        encoding="utf-8",
    )
    (prompts_dir / "summarize.md").write_text(
        "Summarize README.md.\n",
        encoding="utf-8",
    )
    (prompts_dir / "ignore.txt").write_text("ignored\n", encoding="utf-8")
    nested_dir = prompts_dir / "nested"
    nested_dir.mkdir()
    (nested_dir / "ignored.md").write_text("nested prompt\n", encoding="utf-8")

    prompts = SlashCommandStore(prompts_dir=prompts_dir).commands()

    assert list(prompts) == ["/fix-tests", "/summarize"]
    assert [prompt.preview for prompt in prompts.values()] == [
        "Investigate and fix {target}.",
        "Summarize README.md.",
    ]
    assert prompts["/fix-tests"].template == (
        "\n\nInvestigate and fix {target}.\nExplain the root cause briefly.\n"
    )


def test_slash_command_store_returns_empty_when_prompts_dir_is_missing(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".faltoobot"
    root.mkdir(parents=True)

    assert SlashCommandStore(prompts_dir=root / "prompts").commands() == {}


def test_slash_command_store_truncates_long_preview(tmp_path: Path) -> None:
    _root, prompts_dir = make_prompt_root(tmp_path)
    (prompts_dir / "long.md").write_text(
        "Investigate and fix the failing tests in target carefully before touching code today.\nSecond line.\n",
        encoding="utf-8",
    )

    prompts = SlashCommandStore(prompts_dir=prompts_dir).commands()

    assert (
        prompts["/long"].preview == "Investigate and fix the failing tests in target..."
    )


def test_slash_command_store_uses_cache_until_prompt_files_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _root, prompts_dir = make_prompt_root(tmp_path)
    (prompts_dir / "fix-tests.md").write_text(
        "Investigate and fix {target}.\n",
        encoding="utf-8",
    )

    load_calls = 0
    original = slash_commands._discover_slash_commands

    def wrapped(
        prompts_dir: Path,
        excluded_commands: frozenset[str],
    ) -> dict[str, slash_commands.SlashCommand]:
        nonlocal load_calls
        load_calls += 1
        return original(prompts_dir, excluded_commands)

    monkeypatch.setattr(slash_commands, "_discover_slash_commands", wrapped)

    first_load = 1
    second_load = 2
    third_load = 3

    store = SlashCommandStore(prompts_dir=prompts_dir)
    assert store.commands() == {
        "/fix-tests": slash_commands.SlashCommand(
            name="fix-tests",
            path=prompts_dir / "fix-tests.md",
            template="Investigate and fix {target}.\n",
            preview="Investigate and fix {target}.",
        )
    }
    assert load_calls == first_load

    assert store.commands() == {
        "/fix-tests": slash_commands.SlashCommand(
            name="fix-tests",
            path=prompts_dir / "fix-tests.md",
            template="Investigate and fix {target}.\n",
            preview="Investigate and fix {target}.",
        )
    }
    assert load_calls == first_load

    (prompts_dir / "fix-tests.md").write_text(
        "Investigate and fix tests carefully.\n",
        encoding="utf-8",
    )
    assert (
        store.commands()["/fix-tests"].preview == "Investigate and fix tests carefully."
    )
    assert load_calls == second_load

    (prompts_dir / "summarize.md").write_text(
        "Summarize README.md.\n",
        encoding="utf-8",
    )
    assert list(store.commands()) == ["/fix-tests", "/summarize"]
    assert load_calls == third_load
