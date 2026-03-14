import pytest
from textual.app import App, ComposeResult

from faltoobot.chat import TranscriptArea


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
