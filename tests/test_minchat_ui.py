from pathlib import Path
from typing import Any

import pytest
from textual import events

from faltoobot import sessions
from faltoobot.minchat import Composer, FaltooChatApp


def build_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, FaltooChatApp]:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(workspace)
    return workspace, FaltooChatApp(
        session=sessions.get_session(
            chat_key=sessions.get_dir_chat_key(workspace),
            workspace=workspace,
        )
    )


@pytest.mark.anyio
async def test_minchat_shift_enter_keeps_multiline_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, app = build_app(tmp_path, monkeypatch)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("h", "i", "shift+enter", "t", "h", "e", "r", "e")
        composer = app.query_one("#composer", Composer)
        assert composer.text == "hi\nthere"


@pytest.mark.anyio
async def test_minchat_paste_attaches_local_image_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    image = workspace / "cat.png"
    image.write_bytes(b"png")

    async with app.run_test() as pilot:
        await pilot.pause()
        composer = app.query_one("#composer", Composer)
        await composer.on_paste(events.Paste(str(image)))
        assert composer.attachments == [image.resolve()]
        assert str(composer.border_title) == "1 attachment"


@pytest.mark.anyio
async def test_minchat_ctrl_v_attaches_clipboard_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    image = workspace / "clipboard.png"
    image.write_bytes(b"png")
    monkeypatch.setattr("faltoobot.minchat.save_clipboard_image", lambda session: image)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+v")
        await pilot.pause()
        composer = app.query_one("#composer", Composer)
        assert composer.attachments == [image]
        assert str(composer.border_title) == "1 attachment"


@pytest.mark.anyio
async def test_minchat_submits_composer_attachments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    image = workspace / "cat.png"
    image.write_bytes(b"png")
    seen: list[dict[str, Any]] = []

    async def fake_get_answer_streaming(
        *,
        session: sessions.Session,
        question: str,
        attachments: list[sessions.Attachment] | None = None,
    ):
        seen.append(
            {
                "session": session,
                "question": question,
                "attachments": attachments,
            }
        )
        if False:
            yield None

    monkeypatch.setattr(
        "faltoobot.minchat.sessions.get_answer_streaming",
        fake_get_answer_streaming,
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        composer = app.query_one("#composer", Composer)
        composer.load_text("What is this?")
        composer.attach_image(image.resolve())
        await app.submit_message()
        await pilot.pause()
        assert composer.attachments == []

    assert seen == [
        {
            "session": app.session,
            "question": "What is this?",
            "attachments": [image.resolve()],
        }
    ]
