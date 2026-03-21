from pathlib import Path

import pytest

from faltoobot import sessions
from faltoobot.minchat import Composer, FaltooChatApp


@pytest.mark.anyio
async def test_minchat_shift_enter_keeps_multiline_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(workspace)

    app = FaltooChatApp(
        session=sessions.get_session(
            chat_key=sessions.get_dir_chat_key(workspace),
            workspace=workspace,
        )
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("h", "i", "shift+enter", "t", "h", "e", "r", "e")
        composer = app.query_one("#composer", Composer)
        assert composer.text == "hi\nthere"
