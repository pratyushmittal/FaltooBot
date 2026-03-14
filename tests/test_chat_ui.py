import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

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
