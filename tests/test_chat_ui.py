import json
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, Static

from faltoobot.chat import TranscriptArea, build_chat_app


class TranscriptTestApp(App[None]):
    def compose(self) -> ComposeResult:
        yield TranscriptArea("bot> hello world", id="messages", read_only=True, show_cursor=False)


@pytest.mark.anyio
async def test_transcript_double_click_selects_word() -> None:
    app = TranscriptTestApp()
    async with app.run_test() as pilot:
        area = app.query_one(TranscriptArea)
        await pilot.double_click(area, offset=(7, 0))
        assert area.selected_text == "hello"


@pytest.mark.anyio
async def test_chat_shows_model_and_thinking_status() -> None:
    app = build_chat_app()
    async with app.run_test():
        status = app.query_one("#status", Static)
        assert f"model: {app.config.openai_model}" in status.content
        assert f"thinking: {app.config.openai_thinking}" in status.content


@pytest.mark.anyio
async def test_chat_focuses_input_on_mount() -> None:
    app = build_chat_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one(Input).has_focus


@pytest.mark.anyio
async def test_tree_opens_current_session_messages_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_dir = home / ".faltoobot"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(
        "\n".join(
            [
                "# Faltoobot config",
                "",
                "[openai]",
                'api_key = "test-key"',
                'model = "gpt-5.2"',
                'thinking = "none"',
                "",
                "[bot]",
                "allow_groups = false",
                "allowed_chats = []",
                f'system_prompt = {json.dumps("Test prompt.")}',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(workspace)

    opened: list[Path] = []
    monkeypatch.setattr("faltoobot.chat.open_in_default_editor", lambda path: opened.append(path))

    app = build_chat_app()
    async with app.run_test() as pilot:
        input_widget = app.query_one(Input)
        input_widget.value = "/tree"
        input_widget.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert app.session is not None
        assert opened == [app.session.messages_file]
