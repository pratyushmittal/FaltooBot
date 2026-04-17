import asyncio
import os
from pathlib import Path

import pytest

from faltoobot import sessions
from faltoobot.gpt_utils import MessageItem
from faltoobot.faltoochat.app import Composer, FaltooChatApp

MIN_ASSISTANT_MESSAGES = 2


async def wait_for_idle(app: FaltooChatApp) -> None:
    # comment: composer submit schedules a worker, so answering can start a moment later.
    while not app.is_answering:
        await asyncio.sleep(0.05)
    while app.is_answering:
        await asyncio.sleep(0.05)


def config_text() -> str:
    return "\n".join(
        [
            "# Faltoobot config",
            "",
            "[openai]",
            'api_key = ""',
            'model = "gpt-5.4-nano"',
            'thinking = "none"',
            "fast = true",
            "",
            "[bot]",
            "allow_group_chats = []",
            "allowed_chats = []",
            "",
        ]
    )


def write_skill(workspace: Path, name: str, text: str) -> None:
    skills_dir = workspace / ".skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / f"{name}.md").write_text(text, encoding="utf-8")


def message_text(item: MessageItem) -> str:
    content = item.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    return "\n".join(
        text
        for part in content
        if isinstance(part, dict)
        for text in [str(part.get("text") or "").strip()]
        if text
    )


@pytest.mark.anyio
async def test_minchat_streams_ls_and_follow_up_e2e(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if os.environ.get("RUN_FALTOOCHAT_E2E") != "1":
        pytest.skip("Set RUN_FALTOOCHAT_E2E=1 to run the E2E test.")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY must be set to run this E2E test.")

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "alpha.txt").write_text("a", encoding="utf-8")
    (workspace / "beta.txt").write_text("b", encoding="utf-8")

    config_path = home / ".faltoobot" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        config_text(),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(workspace)

    app = FaltooChatApp(
        session=sessions.get_session(
            chat_key=sessions.get_dir_chat_key(workspace),
            workspace=workspace,
        )
    )

    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0)
        composer = app.query_one("#composer", Composer)

        composer.load_text("Run `ls` in the shell tool and reply with one filename.")
        await composer.action_composer_enter()
        await asyncio.wait_for(wait_for_idle(app), timeout=30)
        await pilot.pause(0)

        first_payload = sessions.get_messages(app.session)
        first_items = [
            item for item in first_payload["messages"] if isinstance(item, dict)
        ]
        assert any(
            item.get("type") == "function_call"
            and item.get("name") == "run_shell_call"
            and "ls" in str(item.get("arguments") or "")
            for item in first_items
        )
        assert any(item.get("type") == "function_call_output" for item in first_items)
        assert any(
            item.get("type") == "message"
            and item.get("role") == "assistant"
            and isinstance(item.get("usage"), dict)
            and message_text(item)
            for item in first_items
        )

        composer.load_text("What command did you run?")
        await composer.action_composer_enter()
        await asyncio.wait_for(wait_for_idle(app), timeout=30)
        await pilot.pause(0)

        second_payload = sessions.get_messages(app.session)
        second_items = [
            item for item in second_payload["messages"] if isinstance(item, dict)
        ]
        assistant_messages = [
            item
            for item in second_items
            if item.get("type") == "message" and item.get("role") == "assistant"
        ]
        assert len(assistant_messages) >= MIN_ASSISTANT_MESSAGES
        assert "ls" in message_text(assistant_messages[-1]).lower()


@pytest.mark.anyio
async def test_minchat_loads_local_skill_e2e(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if os.environ.get("RUN_FALTOOCHAT_E2E") != "1":
        pytest.skip("Set RUN_FALTOOCHAT_E2E=1 to run the E2E test.")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY must be set to run this E2E test.")

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_skill(
        workspace,
        "marothon-info",
        "---\ndescription: Marathon training log with distances by date\n---\n"
        "2026-01-05: 8 km\n"
        "2026-01-19: 14 km\n"
        "2026-02-11: 17 km\n"
        "2026-03-03: 6 km\n",
    )

    config_path = home / ".faltoobot" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config_text(), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(workspace)

    app = FaltooChatApp(
        session=sessions.get_session(
            chat_key=sessions.get_dir_chat_key(workspace),
            workspace=workspace,
        )
    )

    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0)
        composer = app.query_one("#composer", Composer)

        composer.load_text(
            "Use the local skill `marothon-info` and tell me how many km you ran on 2026-02-11. "
            "Reply with just the answer."
        )
        await composer.action_composer_enter()
        await asyncio.wait_for(wait_for_idle(app), timeout=30)
        await pilot.pause(0)

        payload = sessions.get_messages(app.session)
        items = [item for item in payload["messages"] if isinstance(item, dict)]
        assert any(
            item.get("type") == "function_call"
            and item.get("name") == "load_skill"
            and "marothon-info" in str(item.get("arguments") or "")
            for item in items
        )
        assistant_messages = [
            item
            for item in items
            if item.get("type") == "message" and item.get("role") == "assistant"
        ]
        assert assistant_messages
        answer = message_text(assistant_messages[-1]).lower()
        assert "17" in answer
