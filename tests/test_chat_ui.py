import argparse
import asyncio
import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from ratatui_py.types import KeyCode, KeyEvt, KeyMods

from faltoobot.chat import (
    ChatUi,
    InputBuffer,
    build_chat_runtime,
    input_hint,
    input_layout,
    main,
    render,
    run_chat,
    transcript_lines,
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


def transcript_text(runtime: object, width: int = 120) -> str:
    entries = runtime.display_entries()  # type: ignore[attr-defined]
    lines = transcript_lines(entries, width)
    return "\n".join(text for text, _ in lines)


class FakeTerminal:
    def __init__(self, events: list[object] | None = None) -> None:
        self.events = list(events or [])
        self.cursor = (0, 0)
        self.frames = 0

    def __enter__(self) -> "FakeTerminal":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def size(self) -> tuple[int, int]:
        return (100, 24)

    def draw_frame(self, cmds: list[object]) -> bool:
        self.frames += len(cmds)
        return True

    def next_event_typed(self, timeout_ms: int) -> object | None:
        _ = timeout_ms
        return self.events.pop(0) if self.events else None

    def set_cursor_position(self, x: int, y: int) -> None:
        self.cursor = (x, y)

    def show_cursor(self) -> None:
        return None


def key(char: str, mods: KeyMods = KeyMods.NONE) -> KeyEvt:
    return KeyEvt(kind="key", code=KeyCode.Char, ch=ord(char), mods=mods)


@pytest.mark.anyio
async def test_input_hint_shows_model_and_thinking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_home(tmp_path, monkeypatch, thinking="medium")
    config = build_config()
    text = input_hint(config)

    assert f"model: {config.openai_model}" in text
    assert f"thinking: {config.openai_thinking}" in text
    assert "Ctrl+J newline" in text


def test_input_buffer_supports_multiline_cursor_moves() -> None:
    buffer = InputBuffer()
    buffer.insert("hello")
    buffer.insert("\n")
    buffer.insert("world")
    buffer.move(-3)
    buffer.backspace()
    buffer.home()
    buffer.insert("X")
    buffer.end()
    buffer.delete()

    lines, cursor = input_layout(buffer.text, 20, buffer.cursor)
    assert lines == ["you> hello", "Xwrld"]
    assert cursor == (5, 1)


@pytest.mark.anyio
async def test_run_chat_handles_exit_command_via_fake_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    config = build_config()
    prompts: list[str] = []

    class FakeRuntime:
        def __init__(self) -> None:
            self.config = config
            self.pending_prompts: list[str] = []
            self.current_reply_task = None
            self.started = False
            self.closed = False
            self.entries: list[object] = []

        async def start(self) -> None:
            self.started = True

        async def close(self) -> None:
            self.closed = True

        async def submit(self, prompt: str) -> bool:
            prompts.append(prompt)
            return prompt != "/exit"

        def interrupt(self) -> bool:
            return False

        def display_entries(self) -> list[object]:
            return self.entries

    runtime = FakeRuntime()
    terminal = FakeTerminal(
        [
            *[key(char) for char in "/exit"],
            KeyEvt(kind="key", code=KeyCode.Enter, ch=0, mods=KeyMods.NONE),
        ]
    )
    monkeypatch.setattr("faltoobot.chat.build_chat_runtime", lambda *args, **kwargs: runtime)

    @contextmanager
    def fake_session() -> FakeTerminal:
        yield terminal

    await run_chat(config=config, session_factory=fake_session)

    assert runtime.started
    assert runtime.closed
    assert prompts == ["/exit"]


def test_main_returns_130_on_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("faltoobot.chat.parse_args", lambda: argparse.Namespace(name=None))

    def fake_run(coro: object) -> None:
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        raise KeyboardInterrupt

    monkeypatch.setattr("faltoobot.chat.asyncio.run", fake_run)

    assert main() == 130


@pytest.mark.anyio
async def test_render_sets_cursor_for_input_buffer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_home(tmp_path, monkeypatch)
    runtime = build_chat_runtime()
    await runtime.start()
    ui = ChatUi(runtime=runtime, input_buffer=InputBuffer(text="hello", cursor=5))
    term = FakeTerminal()

    render(term, ui)
    await runtime.close()

    assert term.frames == 3
    assert term.cursor == (10, 23)


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
    assert str(runtime.session.messages_file) in transcript_text(runtime, width=400)


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
    assert "pwd" in text
    assert "thinking> Checking context." in text
    assert "you> hello" in text
    assert "bot> world" in text


@pytest.mark.anyio
async def test_chat_shows_live_reasoning_tool_and_bot_entries(
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
async def test_chat_caps_tool_context_to_8_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "text": "done",
            "output_items": [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "skills",
                    "arguments": json.dumps({f"k{i}": i for i in range(12)}),
                }
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

    block = text.split("tool> ", 1)[1].split("\nbot> ", 1)[0].splitlines()
    assert len(block) == 8
    assert block[-1].strip() == "..."


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
