import argparse
import asyncio
import json
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console
from rich.text import Text

from faltoobot.chat import (
    Composer,
    build_chat_app,
    build_chat_runtime,
    input_hint,
    main,
    rich_renderable,
)
from faltoobot.config import build_config
from faltoobot.store import add_turn, cli_session, existing_cli_session


def config_text(system_prompt: str, thinking: str = "none") -> str:
    return "\n".join(
        [
            "# Faltoobot config",
            "",
            "[openai]",
            'api_key = "test-key"',
            'model = "gpt-5.2"',
            f'thinking = "{thinking}"',
            "",
            "[bot]",
            "allow_groups = false",
            "allowed_chats = []",
            f"system_prompt = {json.dumps(system_prompt)}",
            "",
        ]
    )


def prepare_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, thinking: str = "none") -> Path:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_dir = home / ".faltoobot"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(config_text("Test prompt.", thinking), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(workspace)
    return workspace


def transcript_text(runtime: object) -> str:
    output = StringIO()
    console = Console(file=output, force_terminal=False, width=300)
    for entry in runtime.display_entries():  # type: ignore[attr-defined]
        console.print(rich_renderable(entry.kind, entry.content))
    return output.getvalue()


def status_plain(app: object) -> str:
    renderable = app.query_one("#status").render()  # type: ignore[attr-defined]
    return renderable.plain if isinstance(renderable, Text) else str(renderable)


def entry_tuples(runtime: object) -> list[tuple[str, str]]:
    return [(entry.kind, entry.content) for entry in runtime.display_entries()]  # type: ignore[attr-defined]


def test_input_hint_shows_model_and_thinking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_home(tmp_path, monkeypatch, thinking="medium")
    config = build_config()
    text = input_hint(config)

    assert f"model: {config.openai_model}" in text
    assert f"thinking: {config.openai_thinking}" in text
    assert "Shift+Enter newline" in text
    assert "Ctrl+C interrupt" in text


def test_main_returns_130_on_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("faltoobot.chat.parse_args", lambda: argparse.Namespace(name=None))

    class FakeApp:
        def run(self) -> None:
            raise KeyboardInterrupt

    monkeypatch.setattr("faltoobot.chat.build_chat_app", lambda **_: FakeApp())
    assert main() == 130


@pytest.mark.anyio
async def test_tree_opens_current_session_messages_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    opened: list[Path] = []
    monkeypatch.setattr("faltoobot.chat.open_in_default_editor", lambda path: opened.append(path))

    runtime = build_chat_runtime()
    await runtime.start()
    assert runtime.session is not None
    await runtime.submit("/tree")
    await runtime.close()

    assert opened == [runtime.session.messages_file]
    assert str(runtime.session.messages_file) in transcript_text(runtime)


@pytest.mark.anyio
async def test_chat_replays_existing_session_messages_on_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = prepare_home(tmp_path, monkeypatch)
    config = build_config()
    session = cli_session(config.sessions_dir, "CLI test", workspace)
    session = add_turn(session, "user", "hello")
    add_turn(
        session,
        "assistant",
        "world",
        items=[
            {"type": "shell_call", "call_id": "call_1", "action": {"commands": ["pwd"]}},
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Checking context."}]},
        ],
    )

    runtime = build_chat_runtime()
    await runtime.start()
    text = transcript_text(runtime)
    await runtime.close()

    assert "tool> shell" in text
    assert "\npwd" in text
    assert "thinking> Checking context." in text
    assert "you> hello" in text
    assert "bot> world" in text


@pytest.mark.anyio
async def test_chat_shows_thinking_tool_and_bot_for_live_reply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch, thinking="medium")

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        await kwargs["on_reasoning_delta"]("**Planning** reply")
        await kwargs["on_output_item"](
            {"type": "shell_call", "call_id": "call_1", "action": {"commands": ["pwd"]}}
        )
        await kwargs["on_text_delta"]("done")
        return {
            "text": "done",
            "output_items": [
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Planning reply"}]},
                {"type": "shell_call", "call_id": "call_1", "action": {"commands": ["pwd"]}},
            ],
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_stream_reply)

    runtime = build_chat_runtime()
    await runtime.start()
    await runtime.submit("hi")
    await runtime.wait_until_idle()
    text = transcript_text(runtime)
    await runtime.close()

    assert "you> hi" in text
    assert "thinking> Planning reply" in text
    assert text.count("tool> shell") == 1
    assert "bot> done" in text


@pytest.mark.anyio
async def test_reset_creates_new_session_for_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = prepare_home(tmp_path, monkeypatch)
    config = build_config()
    original = cli_session(config.sessions_dir, "CLI original", workspace)
    original = add_turn(original, "user", "first")
    add_turn(original, "assistant", "reply")

    runtime = build_chat_runtime()
    await runtime.start()
    original_id = runtime.session.id if runtime.session else ""
    assert await runtime.submit("/reset")
    await runtime.close()

    assert runtime.session is not None
    assert runtime.session.id != original_id
    assert runtime.session.workspace == workspace
    assert runtime.session.messages == ()
    assert existing_cli_session(config.sessions_dir, workspace).id == runtime.session.id  # type: ignore[union-attr]
    assert "new session:" in transcript_text(runtime)


@pytest.mark.anyio
async def test_chat_queues_messages_while_reply_is_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    started = asyncio.Event()
    release = asyncio.Event()
    prompts: list[str] = []

    async def fake_reply(*args: object, **kwargs: object) -> dict[str, object]:
        session = args[2]
        prompts.append(session.messages[-1].content)
        if len(prompts) == 1:
            started.set()
            await release.wait()
        return {
            "text": f"reply:{prompts[-1]}",
            "output_items": [],
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_reply)

    runtime = build_chat_runtime()
    await runtime.start()
    assert await runtime.submit("first")
    await started.wait()
    assert await runtime.submit("second")
    release.set()
    await runtime.wait_until_idle()
    text = transcript_text(runtime)
    await runtime.close()

    assert prompts == ["first", "second"]
    assert "bot> reply:first" in text
    assert "bot> reply:second" in text


@pytest.mark.anyio
async def test_chat_can_interrupt_inflight_reply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_reply(*args: object, **kwargs: object) -> dict[str, object]:
        await kwargs["on_text_delta"]("partial")
        started.set()
        await release.wait()
        return {
            "text": "partial done",
            "output_items": [],
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_reply)

    runtime = build_chat_runtime()
    await runtime.start()
    assert await runtime.submit("first")
    await started.wait()
    assert runtime.interrupt()
    release.set()
    await runtime.wait_until_idle()
    text = transcript_text(runtime)
    await runtime.close()

    assert "bot> partial" in text
    assert "reply interrupted" in text


@pytest.mark.anyio
async def test_textual_app_focuses_composer_and_shows_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch, thinking="medium")
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.focused, Composer)
        assert "model: gpt-5.2" in status_plain(app)
        assert "thinking: medium" in status_plain(app)


@pytest.mark.anyio
async def test_textual_app_submits_prompt_and_updates_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        await kwargs["on_text_delta"]("pong")
        return {
            "text": "pong",
            "output_items": [],
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_stream_reply)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p", "i", "n", "g", "enter")
        await app.runtime.wait_until_idle()
        await pilot.pause()
        assert ("you", "ping") in entry_tuples(app.runtime)
        assert ("bot", "pong") in entry_tuples(app.runtime)
        assert app.query_one("#composer", Composer).text == ""


@pytest.mark.anyio
async def test_textual_app_shift_enter_keeps_multiline_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("h", "i", "shift+enter", "t", "h", "e", "r", "e")
        composer = app.query_one("#composer", Composer)
        assert composer.text == "hi\nthere"
