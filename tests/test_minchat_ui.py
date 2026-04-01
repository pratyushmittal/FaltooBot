import asyncio
from pathlib import Path
from typing import Any

import pytest
from textual import events

from faltoobot import sessions
from faltoobot.faltoochat import submit_queue
from faltoobot.faltoochat.app import Composer, FaltooChatApp
from faltoobot.session_utils import get_local_user_message_item
from faltoobot.faltoochat.review import ReviewView
from faltoobot.faltoochat.widgets import QueueWidget
from textual.widgets import Markdown, OptionList, TabbedContent


async def wait_for_condition(check: Any) -> None:
    while True:
        if check():
            return
        await asyncio.sleep(0)


def test_minchat_uses_terminal_theme_on_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "faltoobot.faltoochat.app.textual_theme_from_terminal",
        lambda: "textual-light",
    )
    _, app = build_app(tmp_path, monkeypatch)

    assert app.theme == "textual-light"


@pytest.mark.anyio
async def test_minchat_persists_and_restores_selected_theme(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(
        "faltoobot.faltoochat.app.textual_theme_from_terminal",
        lambda: "textual-dark",
    )

    first_app = FaltooChatApp(
        session=sessions.get_session(
            chat_key=sessions.get_dir_chat_key(workspace),
            workspace=workspace,
        )
    )

    async with first_app.run_test() as pilot:
        await pilot.pause()
        first_app.theme = "textual-light"
        await pilot.pause()

    second_app = FaltooChatApp(
        session=sessions.get_session(
            chat_key=sessions.get_dir_chat_key(workspace),
            workspace=workspace,
        )
    )

    assert second_app.theme == "textual-light"


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
async def test_minchat_shows_slash_command_suggestions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, app = build_app(tmp_path, monkeypatch)

    async with app.run_test() as pilot:
        await pilot.pause(0)
        composer = app.query_one("#composer", Composer)
        composer.focus()
        composer.insert("/")
        await pilot.pause(0)

        option_list = app.query_one("#slash-commands", OptionList)
        assert option_list.display
        assert [str(option.prompt) for option in option_list.options] == [
            "/reset — start a fresh session",
            "/tree — open the current session messages file",
        ]


@pytest.mark.anyio
async def test_minchat_up_down_navigate_slash_command_suggestions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, app = build_app(tmp_path, monkeypatch)

    async with app.run_test() as pilot:
        await pilot.pause(0)
        composer = app.query_one("#composer", Composer)
        composer.focus()
        composer.insert("/")
        await pilot.pause(0)

        option_list = app.query_one("#slash-commands", OptionList)
        assert option_list.highlighted == 0

        await pilot.press("down")
        await pilot.pause(0)
        assert option_list.highlighted == 1

        await pilot.press("up")
        await pilot.pause(0)
        assert option_list.highlighted == 0


@pytest.mark.anyio
async def test_minchat_enter_applies_highlighted_slash_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, app = build_app(tmp_path, monkeypatch)

    async with app.run_test() as pilot:
        await pilot.pause(0)
        composer = app.query_one("#composer", Composer)
        composer.focus()
        composer.insert("/re")
        await pilot.pause(0)

        await composer.action_composer_enter()
        await pilot.pause(0)

        assert composer.text == "/reset"


@pytest.mark.anyio
async def test_minchat_has_chat_and_review_tabs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, app = build_app(tmp_path, monkeypatch)

    async with app.run_test() as pilot:
        await pilot.pause(0)
        tabs = app.query_one(TabbedContent)
        assert tabs.active == "chat-tab"
        assert app.query_one(ReviewView)

        tabs.active = "review-tab"
        await pilot.pause(0)
        assert tabs.active == "review-tab"


@pytest.mark.anyio
async def test_minchat_ctrl_2_opens_review_tab(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, app = build_app(tmp_path, monkeypatch)

    async with app.run_test() as pilot:
        await pilot.pause(0)
        tabs = app.query_one(TabbedContent)
        assert tabs.active == "chat-tab"

        await pilot.press("ctrl+2")
        await pilot.pause(0)
        assert tabs.active == "review-tab"


@pytest.mark.anyio
async def test_minchat_ctrl_r_opens_review_tab(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, app = build_app(tmp_path, monkeypatch)

    async with app.run_test() as pilot:
        await pilot.pause(0)
        tabs = app.query_one(TabbedContent)
        assert tabs.active == "chat-tab"

        await pilot.press("ctrl+r")
        await pilot.pause(0)
        assert tabs.active == "review-tab"


@pytest.mark.anyio
async def test_minchat_ctrl_r_toggles_back_to_chat_tab(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, app = build_app(tmp_path, monkeypatch)

    async with app.run_test() as pilot:
        await pilot.pause(0)
        tabs = app.query_one(TabbedContent)

        await pilot.press("ctrl+r")
        await pilot.pause(0)
        assert tabs.active == "review-tab"

        await pilot.press("ctrl+r")
        await pilot.pause(0)
        assert tabs.active == "chat-tab"


@pytest.mark.anyio
async def test_minchat_returning_to_chat_scrolls_transcript_to_bottom(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, app = build_app(tmp_path, monkeypatch)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0)
        transcript = app.query_one("#transcript")
        await transcript.mount(
            *(Markdown(f"line {index}\n\nmore") for index in range(40))
        )
        await pilot.pause(0)
        transcript.scroll_home(animate=False)
        await pilot.pause(0)
        assert transcript.scroll_y == 0

        await pilot.press("ctrl+2")
        await pilot.pause(0)
        await pilot.press("ctrl+1")
        await wait_for_condition(lambda: transcript.scroll_y == transcript.max_scroll_y)

        assert app.query_one(TabbedContent).active == "chat-tab"


@pytest.mark.anyio
async def test_minchat_shift_enter_keeps_multiline_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, app = build_app(tmp_path, monkeypatch)

    async with app.run_test() as pilot:
        await pilot.pause(0)
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
        await pilot.pause(0)
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
    monkeypatch.setattr(
        "faltoobot.faltoochat.app.save_clipboard_image", lambda session: image
    )

    async with app.run_test() as pilot:
        await pilot.pause(0)
        await pilot.press("ctrl+v")
        await pilot.pause(0)
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
        "faltoobot.faltoochat.app.sessions.get_answer_streaming",
        fake_get_answer_streaming,
    )

    async with app.run_test() as pilot:
        await pilot.pause(0)
        composer = app.query_one("#composer", Composer)
        composer.load_text("What is this?")
        composer.attach_image(image.resolve())
        await composer.action_composer_enter()
        await pilot.pause(0)
        assert composer.attachments == []

    assert seen == [
        {
            "session": app.session,
            "question": "What is this?",
            "attachments": [image.resolve()],
        }
    ]


@pytest.mark.anyio
async def test_minchat_load_all_button_loads_full_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, app = build_app(tmp_path, monkeypatch)
    total_messages = 101
    startup_messages = 100
    messages_json = sessions.get_messages(app.session)
    messages_json["messages"] = [
        {"type": "message", "role": "user", "content": f"prompt {index}"}
        for index in range(total_messages)
    ]
    sessions.set_messages(app.session, messages_json)

    async with app.run_test() as pilot:
        await pilot.pause(0)
        transcript = app.query_one("#transcript")
        assert len(transcript.query(Markdown)) == startup_messages

        await app.action_load_all_messages()
        await pilot.pause(0)
        assert len(transcript.query(Markdown)) == total_messages


@pytest.mark.anyio
async def test_minchat_queues_messages_while_streaming(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, app = build_app(tmp_path, monkeypatch)
    started = asyncio.Event()
    release = asyncio.Event()
    seen: list[str] = []

    async def fake_get_answer_streaming(
        *,
        session: sessions.Session,
        question: str,
        attachments: list[sessions.Attachment] | None = None,
    ):
        seen.append(question)
        yield type("Event", (), {"type": "response.output_text.delta", "delta": "hi"})()
        if question == "hello":
            started.set()
            await release.wait()
        yield type("Event", (), {"type": "response.output_text.done"})()

    monkeypatch.setattr(
        "faltoobot.faltoochat.app.sessions.get_answer_streaming",
        fake_get_answer_streaming,
    )

    async with app.run_test() as pilot:
        composer = app.query_one("#composer", Composer)
        composer.load_text("hello")
        await composer.action_composer_enter()
        await asyncio.wait_for(started.wait(), timeout=3)
        await pilot.pause(0)
        assert str(composer.border_subtitle) == "answering"

        composer.load_text("later")
        await composer.action_composer_enter()
        await pilot.pause(0)
        queue = submit_queue.get_queue(app.session)
        assert [item["id"] for item in queue]
        assert queue[0]["auto_submit"] is True
        assert app.query_one("#queue").display

        release.set()
        await asyncio.wait_for(
            wait_for_condition(
                lambda: not app.is_answering and seen == ["hello", "later"]
            ),
            timeout=3,
        )
        await pilot.pause(0)
        assert str(composer.border_subtitle) == ""
        assert submit_queue.get_queue(app.session) == []
        assert seen == ["hello", "later"]


def test_get_local_user_message_item_keeps_local_image_paths() -> None:
    message = get_local_user_message_item(
        "hello",
        [Path("/tmp/cat.png")],
    )

    assert message == {
        "type": "message",
        "role": "user",
        "content": [
            {"type": "input_text", "text": "hello"},
            {"type": "input_image", "image_path": "/tmp/cat.png"},
        ],
    }


@pytest.mark.anyio
async def test_minchat_queue_widget_keybindings_update_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, app = build_app(tmp_path, monkeypatch)
    submit_queue.add_to_queue(
        app.session,
        {"type": "message", "role": "user", "content": "first"},
    )
    submit_queue.add_to_queue(
        app.session,
        {"type": "message", "role": "user", "content": "second"},
    )

    async with app.run_test() as pilot:
        await pilot.pause(0)
        queue_widget = app.query_one(QueueWidget)
        queue_widget.focus()

        await pilot.press("shift+down")
        await pilot.pause(0)
        queue = submit_queue.get_queue(app.session)
        assert [item["content"] for item in queue] == ["second", "first"]

        await pilot.press("delete")
        await pilot.pause(0)
        queue = submit_queue.get_queue(app.session)
        assert [item["content"] for item in queue] == ["second"]


@pytest.mark.anyio
async def test_minchat_queue_enter_loads_selected_message_back_into_composer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, app = build_app(tmp_path, monkeypatch)
    submit_queue.add_to_queue(
        app.session,
        get_local_user_message_item("draft this again", ["/tmp/cat.png"]),
    )

    async with app.run_test() as pilot:
        await pilot.pause(0)
        queue_widget = app.query_one(QueueWidget)
        queue_widget.focus()

        await pilot.press("enter")
        await pilot.pause(0)

        composer = app.query_one("#composer", Composer)
        assert composer.text == "draft this again"
        assert composer.attachments == [Path("/tmp/cat.png")]
        assert composer.border_title == "1 attachment"
        assert submit_queue.get_queue(app.session) == []


@pytest.mark.anyio
async def test_minchat_keeps_answer_text_out_of_thinking_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, app = build_app(tmp_path, monkeypatch)

    async def fake_get_answer_streaming(
        *,
        session: sessions.Session,
        question: str,
        attachments: list[sessions.Attachment] | None = None,
    ):
        yield type(
            "Event",
            (),
            {
                "type": "response.reasoning_summary_part.added",
                "part": type(
                    "Part",
                    (),
                    {"text": "**Planning** answer\n\nHidden detail"},
                )(),
            },
        )()
        yield type("Event", (), {"type": "response.reasoning_summary_part.done"})()
        yield type(
            "Event", (), {"type": "response.output_text.delta", "delta": "Final answer"}
        )()
        yield type("Event", (), {"type": "response.output_text.done"})()

    monkeypatch.setattr(
        "faltoobot.faltoochat.app.sessions.get_answer_streaming",
        fake_get_answer_streaming,
    )

    expected_blocks = 3

    async with app.run_test() as pilot:
        await pilot.pause(0)
        composer = app.query_one("#composer", Composer)
        composer.load_text("hello")
        await composer.action_composer_enter()
        await wait_for_condition(
            lambda: (
                not app.is_answering
                and len(app.query_one("#transcript").children) >= expected_blocks
            )
        )
        await pilot.pause(0)
        transcript = app.query_one("#transcript")
        blocks = [block for block in transcript.query(Markdown)]
        thinking = [block for block in blocks if block.has_class("thinking")]
        answer = [block for block in blocks if block.has_class("answer")]
        assert thinking
        assert answer
        assert "Final answer" not in thinking[-1]._markdown
        assert answer[-1]._markdown == "Final answer"


@pytest.mark.anyio
async def test_minchat_answer_completion_does_not_focus_composer_outside_chat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, app = build_app(tmp_path, monkeypatch)
    release = asyncio.Event()

    async def fake_get_answer_streaming(
        *,
        session: sessions.Session,
        question: str,
        attachments: list[sessions.Attachment] | None = None,
    ):
        yield type("Event", (), {"type": "response.output_text.delta", "delta": "hi"})()
        await release.wait()
        yield type("Event", (), {"type": "response.output_text.done"})()

    monkeypatch.setattr(
        "faltoobot.faltoochat.app.sessions.get_answer_streaming",
        fake_get_answer_streaming,
    )

    async with app.run_test() as pilot:
        composer = app.query_one("#composer", Composer)
        composer.load_text("hello")
        await composer.action_composer_enter()
        await wait_for_condition(lambda: app.is_answering)
        await pilot.press("ctrl+2")
        await pilot.pause(0)

        release.set()
        await asyncio.wait_for(
            wait_for_condition(lambda: not app.is_answering), timeout=3
        )
        await pilot.pause(0)

        assert app.query_one(TabbedContent).active == "review-tab"
        assert app.screen.focused is not composer
