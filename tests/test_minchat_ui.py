import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
from textual import events

from faltoobot import sessions
from faltoobot.faltoochat import submit_queue
from faltoobot.faltoochat.app import Composer, FaltooChatApp
from faltoobot.session_utils import (
    decompose_local_message_item,
    get_local_user_message_item,
)
from faltoobot.faltoochat.review import ReviewView
from faltoobot.faltoochat.widgets import (
    QueueWidget,
    SessionPicker,
    SlashCommandsOptionList,
    TextInputModal,
)
from faltoobot.faltoochat.widgets.search_file import SearchFile
from textual.widgets import Input, Markdown, OptionList, TabbedContent


def _listed_name(session: sessions.Session, name: str) -> str:
    return sessions._session_label(name, session.messages_path)


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


def _write_prompt(tmp_path: Path, name: str, template: str) -> None:
    prompts_dir = tmp_path / "home" / ".faltoobot" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / f"{name}.md").write_text(template, encoding="utf-8")


def _capture_submissions(app: FaltooChatApp) -> list[str]:
    seen: list[str] = []

    async def fake_handle_message(message_item: Any) -> None:
        seen.append(decompose_local_message_item(message_item)[0])

    app.handle_message = cast(Any, fake_handle_message)
    return seen


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

        option_list = app.query_one("#slash-commands", SlashCommandsOptionList)
        assert option_list.display
        assert [str(option.prompt) for option in option_list.options] == [
            "/name — name the current session",
            "/reset — start a fresh session",
            "/resume — resume another session",
            "/status — show bot status",
            "/tree — open the current session messages file",
        ]


@pytest.mark.anyio
async def test_minchat_name_command_opens_modal_and_saves_session_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, app = build_app(tmp_path, monkeypatch)

    async with app.run_test() as pilot:
        await pilot.pause(0)
        composer = app.query_one("#composer", Composer)
        composer.load_text("/name")

        await composer.action_composer_enter()
        await pilot.pause(0)

        modal = app.screen
        assert isinstance(modal, TextInputModal)
        name_input = modal.query_one("#text-input-input", Input)
        name_input.value = ""
        await pilot.click(name_input)
        await pilot.press(
            "F",
            "i",
            "x",
            "space",
            "f",
            "l",
            "a",
            "k",
            "y",
            "space",
            "t",
            "e",
            "s",
            "t",
            "s",
            "enter",
        )
        await wait_for_condition(
            lambda: (
                sessions.list_sessions(app.session.chat_key)
                == [
                    {
                        "id": app.session.session_id,
                        "name": _listed_name(app.session, "Fix flaky tests"),
                    }
                ]
            )
        )

        assert sessions.list_sessions(app.session.chat_key) == [
            {
                "id": app.session.session_id,
                "name": _listed_name(app.session, "Fix flaky tests"),
            }
        ]
        transcript = app.query_one("#transcript")
        assert any(
            "Saved session name: Fix flaky tests" in block._markdown
            for block in transcript.query(Markdown)
        )


@pytest.mark.anyio
async def test_minchat_name_command_notifies_when_name_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    existing = sessions.get_session(
        chat_key=app.session.chat_key,
        session_id="Existing",
        workspace=workspace,
    )
    notifications: list[tuple[str, str]] = []

    def fake_notify(
        message: str,
        *,
        title: str = "",
        severity: str = "information",
        timeout: int | float | None = None,
        markup: bool = True,
    ) -> None:
        notifications.append((message, severity))

    app.notify = cast(Any, fake_notify)

    async with app.run_test() as pilot:
        await pilot.pause(0)
        composer = app.query_one("#composer", Composer)
        composer.load_text("/name")

        await composer.action_composer_enter()
        await pilot.pause(0)

        modal = app.screen
        assert isinstance(modal, TextInputModal)
        name_input = modal.query_one("#text-input-input", Input)
        name_input.value = existing.session_id
        await pilot.click(name_input)
        await pilot.press("enter")
        await wait_for_condition(lambda: bool(notifications))

    assert notifications == [
        ("Could not rename session: Session already exists: Existing", "error")
    ]
    assert app.session.session_id != existing.session_id


@pytest.mark.anyio
async def test_minchat_resume_command_opens_picker_and_switches_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    current_messages = sessions.get_messages(app.session)
    current_messages["messages"] = [
        {"type": "message", "role": "user", "content": "current session"}
    ]
    sessions.set_messages(app.session, current_messages)
    target = sessions.get_session(
        chat_key=app.session.chat_key,
        session_id="target-session",
        workspace=workspace,
    )
    target_messages = sessions.get_messages(target)
    target_messages["messages"] = [
        {"type": "message", "role": "user", "content": "resume target"}
    ]
    sessions.set_messages(target, target_messages)
    sessions.set_session_name(target, "Fix flaky tests")

    async with app.run_test() as pilot:
        await pilot.pause(0)
        composer = app.query_one("#composer", Composer)
        composer.load_text("/resume")

        await composer.action_composer_enter()
        await pilot.pause(0)

        modal = app.screen
        assert isinstance(modal, SessionPicker)
        search_input = modal.query_one("#telescope-input", Input)
        await pilot.click(search_input)
        await pilot.press("F", "i", "x")
        await wait_for_condition(
            lambda: len(modal.query_one("#telescope-options", OptionList).options) == 1
        )
        await pilot.press("enter")
        await wait_for_condition(lambda: app.session.session_id == target.session_id)

        transcript = app.query_one("#transcript")
        assert app.session.session_id == target.session_id
        assert [block._markdown for block in transcript.query(Markdown)] == [
            "resume target"
        ]


@pytest.mark.anyio
async def test_minchat_enter_completes_partial_custom_slash_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_prompt(tmp_path, "fix-tests", "Investigate and fix {target}.")
    _, app = build_app(tmp_path, monkeypatch)
    seen = _capture_submissions(app)

    async with app.run_test() as pilot:
        await pilot.pause(0)
        composer = app.query_one("#composer", Composer)
        composer.focus()
        composer.insert("/fi")
        await pilot.pause(0)

        await composer.action_composer_enter()
        await pilot.pause(0)

        assert seen == []
        assert composer.text == "/fix-tests"
        assert composer.cursor_location == (0, len("/fix-tests"))


@pytest.mark.anyio
@pytest.mark.parametrize("submission_mode", ["click", "direct"])
async def test_minchat_custom_slash_command_submission_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    submission_mode: str,
) -> None:
    _write_prompt(tmp_path, "fix-tests", "Investigate and fix {target}.")
    _, app = build_app(tmp_path, monkeypatch)
    seen = _capture_submissions(app)

    async with app.run_test() as pilot:
        await pilot.pause(0)
        composer = app.query_one("#composer", Composer)
        composer.focus()

        if submission_mode == "click":
            composer.insert("/fi")
            await pilot.pause(0)
            option_list = app.query_one("#slash-commands", SlashCommandsOptionList)
            await option_list.on_option_list_option_selected(
                OptionList.OptionSelected(option_list, option_list.options[0], 0)
            )
        else:
            composer.load_text("/fix-tests")
            await composer.action_composer_enter()
        await pilot.pause(0)

        assert seen == ["Investigate and fix {target}."]
        assert composer.text == ""


@pytest.mark.anyio
async def test_minchat_custom_slash_command_with_extra_text_submits_raw_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_prompt(tmp_path, "summarize", "Summarize {file} for {topic}.")
    _, app = build_app(tmp_path, monkeypatch)
    seen = _capture_submissions(app)

    async with app.run_test() as pilot:
        await pilot.pause(0)
        composer = app.query_one("#composer", Composer)
        composer.focus()
        composer.load_text("/summarize file=README.md")

        await composer.action_composer_enter()
        await pilot.pause(0)

        assert seen == ["/summarize file=README.md"]


@pytest.mark.anyio
async def test_minchat_at_opens_file_picker_and_inserts_mention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, app = build_app(tmp_path, monkeypatch)
    (workspace / "alpha.py").write_text("value = 1\n", encoding="utf-8")
    (workspace / "beta.py").write_text("value = 2\n", encoding="utf-8")

    async with app.run_test() as pilot:
        await pilot.pause(0)
        composer = app.query_one("#composer", Composer)
        composer.focus()

        await pilot.press("@")
        await pilot.pause(0)
        modal = app.screen
        assert isinstance(modal, SearchFile)
        search_input = modal.query_one("#telescope-input", Input)
        await pilot.click(search_input)
        await pilot.press("b", "e", "t", "a")
        await wait_for_condition(
            lambda: len(modal.query_one("#telescope-options", OptionList).options) == 1
        )
        await pilot.press("enter")
        await wait_for_condition(
            lambda: app.query_one("#composer", Composer).text == "`beta.py` "
        )

        assert app.query_one("#composer", Composer).text == "`beta.py` "


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
        assert composer.cursor_location == (0, len("/reset"))


@pytest.mark.anyio
async def test_minchat_status_command_shows_config_status_and_last_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, app = build_app(tmp_path, monkeypatch)
    messages_json = sessions.get_messages(app.session)
    messages_json["messages"] = [
        {
            "type": "message",
            "role": "assistant",
            "content": "answer",
            "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 5},
        }
    ]
    sessions.set_messages(app.session, messages_json)

    async with app.run_test() as pilot:
        await pilot.pause(0)
        composer = app.query_one("#composer", Composer)
        composer.focus()
        composer.load_text("/status")
        await composer.action_composer_enter()
        await pilot.pause(0)

        transcript = app.query_one("#transcript")
        blocks = [block for block in transcript.query(Markdown)]
        assert any("Faltoobot status" in block._markdown for block in blocks)
        assert any("openai_model" in block._markdown for block in blocks)
        assert any("Session" in block._markdown for block in blocks)
        assert any(
            f'session_id="{app.session.session_id}"' in block._markdown
            for block in blocks
        )
        assert any(
            f'workspace="{app.workspace}"' in block._markdown for block in blocks
        )
        assert any("Session usage" in block._markdown for block in blocks)
        assert any('"total_tokens": 5' in block._markdown for block in blocks)


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
async def test_composer_alt_arrows_scroll_transcript_by_user_and_answer_messages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, app = build_app(tmp_path, monkeypatch)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0)
        transcript = app.query_one("#transcript")
        composer = app.query_one("#composer", Composer)
        await transcript.remove_children()
        await transcript.mount(
            *(Markdown(f"filler before {index}\n\nmore") for index in range(20)),
            Markdown("first user\n\nmore", classes="user"),
            Markdown("tool output\n\nmore", classes="tool"),
            Markdown("first answer\n\nmore", classes="answer"),
            Markdown("another tool\n\nmore", classes="tool"),
            Markdown("second user\n\nmore", classes="user"),
            Markdown("second answer\n\nmore", classes="answer"),
            *(Markdown(f"filler after {index}\n\nmore") for index in range(20)),
        )
        await pilot.pause(0)
        composer.focus()
        transcript.scroll_end(animate=False, immediate=True)
        await pilot.pause(0)

        original_scroll_to = transcript.scroll_to
        jumps: list[float] = []

        def scroll_to(
            *args: Any, y: float | None = None, animate: bool = True, **kwargs: Any
        ):
            if y is not None:
                jumps.append(y)
            return original_scroll_to(*args, y=y, animate=animate, **kwargs)

        monkeypatch.setattr(transcript, "scroll_to", scroll_to)

        composer.action_transcript_previous_message()
        composer.action_transcript_previous_message()

        assert jumps == [
            transcript.children[25].virtual_region.y,
            transcript.children[24].virtual_region.y,
        ]

        jumps.clear()
        transcript.scroll_to(
            y=transcript.children[23].virtual_region.y, animate=False, immediate=True
        )
        await pilot.pause(0)
        jumps.clear()

        composer.action_transcript_previous_message()

        assert jumps == [transcript.children[22].virtual_region.y]

        transcript.scroll_end(animate=False, immediate=True)
        await pilot.pause(0)
        jumps.clear()

        composer.action_transcript_previous_message()

        assert jumps == [transcript.children[25].virtual_region.y]


@pytest.mark.anyio
async def test_composer_alt_up_skips_visible_clamped_last_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, app = build_app(tmp_path, monkeypatch)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0)
        transcript = app.query_one("#transcript")
        composer = app.query_one("#composer", Composer)
        await transcript.remove_children()
        await transcript.mount(
            *(Markdown(f"filler before {index}\n\nmore") for index in range(30)),
            Markdown("older answer\n\nmore", classes="answer"),
            *(Markdown(f"filler middle {index}\n\nmore") for index in range(10)),
            Markdown("latest short answer", classes="answer"),
        )
        await pilot.pause(0)
        transcript.scroll_end(animate=False, immediate=True)
        await pilot.pause(0)

        older_answer_y = transcript.children[30].virtual_region.y
        latest_answer_y = transcript.children[-1].virtual_region.y
        assert latest_answer_y > transcript.max_scroll_y

        original_scroll_to = transcript.scroll_to
        jumps: list[float] = []

        def scroll_to(
            *args: Any, y: float | None = None, animate: bool = True, **kwargs: Any
        ):
            if y is not None:
                jumps.append(y)
            return original_scroll_to(*args, y=y, animate=animate, **kwargs)

        monkeypatch.setattr(transcript, "scroll_to", scroll_to)

        composer.action_transcript_previous_message()

        assert jumps == [older_answer_y]
        assert jumps != [latest_answer_y]


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

    async def fake_append_user_turn(
        session: sessions.Session,
        *,
        question: str,
        attachments: list[sessions.Attachment] | None = None,
        message_ids: list[str] | None = None,
    ) -> bool:
        seen.append(
            {
                "session": session,
                "question": question,
                "attachments": attachments,
            }
        )
        return True

    async def fake_get_answer_streaming(session: sessions.Session):
        if False:
            yield None

    monkeypatch.setattr(
        "faltoobot.faltoochat.app.sessions.append_user_turn",
        fake_append_user_turn,
    )
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

    async def fake_get_answer_streaming(session: sessions.Session):
        question = str(sessions.get_messages(session)["messages"][-1]["content"])
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

    async def fake_get_answer_streaming(session: sessions.Session):
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
async def test_minchat_bells_when_answer_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, app = build_app(tmp_path, monkeypatch)
    release = asyncio.Event()
    bells: list[bool] = []

    async def fake_get_answer_streaming(session: sessions.Session):
        yield type("Event", (), {"type": "response.output_text.delta", "delta": "hi"})()
        await release.wait()
        yield type("Event", (), {"type": "response.output_text.done"})()

    monkeypatch.setattr(
        "faltoobot.faltoochat.app.sessions.get_answer_streaming",
        fake_get_answer_streaming,
    )
    monkeypatch.setattr(app, "bell", lambda: bells.append(True))

    async with app.run_test() as pilot:
        composer = app.query_one("#composer", Composer)
        composer.load_text("hello")
        await composer.action_composer_enter()
        await wait_for_condition(lambda: app.is_answering)
        assert bells == []

        release.set()
        await asyncio.wait_for(
            wait_for_condition(lambda: not app.is_answering), timeout=3
        )
        await pilot.pause(0)
        assert bells == [True]


@pytest.mark.anyio
async def test_minchat_answer_completion_does_not_focus_composer_outside_chat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, app = build_app(tmp_path, monkeypatch)
    release = asyncio.Event()

    async def fake_get_answer_streaming(session: sessions.Session):
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
