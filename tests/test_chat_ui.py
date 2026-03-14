import argparse
import asyncio
import json
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from faltoobot.chat import build_chat_runtime, input_hint, main, run_chat
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


def runtime_console() -> tuple[Console, StringIO]:
    output = StringIO()
    return Console(file=output, force_terminal=False, width=300), output


def test_input_hint_shows_model_and_thinking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_home(tmp_path, monkeypatch, thinking="medium")
    config = build_config()
    text = input_hint(config)

    assert f"model: {config.openai_model}" in text
    assert f"thinking: {config.openai_thinking}" in text
    assert "Enter send" in text
    assert "Ctrl+C interrupt" in text


@pytest.mark.anyio
async def test_run_chat_uses_plain_input_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    config = build_config()
    prompts: list[str] = []

    class FakeRuntime:
        def __init__(self) -> None:
            self.config = config
            self.console = Console(file=StringIO(), force_terminal=False, width=80)
            self.processing_task: asyncio.Task[None] | None = None
            self.status_calls = 0
            self.started = False
            self.closed = False

        async def start(self) -> None:
            self.started = True

        async def close(self) -> None:
            self.closed = True

        def write_status(self) -> None:
            self.status_calls += 1

        async def submit(self, prompt: str) -> bool:
            return prompt != "/exit"

        async def wait_until_idle(self) -> None:
            self.processing_task = None

        def interrupt(self) -> bool:
            return False

    runtime = FakeRuntime()
    monkeypatch.setattr("faltoobot.chat.build_chat_runtime", lambda *args, **kwargs: runtime)

    async def fake_read_input(prompt: str) -> str:
        prompts.append(prompt)
        return "/exit"

    monkeypatch.setattr("faltoobot.chat.read_input", fake_read_input)

    await run_chat(config=config)

    assert runtime.started
    assert runtime.closed
    assert runtime.status_calls == 1
    assert prompts == ["you> "]


@pytest.mark.anyio
async def test_run_chat_exits_cleanly_on_keyboard_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    config = build_config()

    class FakeRuntime:
        def __init__(self) -> None:
            self.config = config
            self.console = Console(file=StringIO(), force_terminal=False, width=80)
            self.processing_task: asyncio.Task[None] | None = None
            self.started = False
            self.closed = False

        async def start(self) -> None:
            self.started = True

        async def close(self) -> None:
            self.closed = True

        def write_status(self) -> None:
            return None

        async def submit(self, prompt: str) -> bool:
            return True

        async def wait_until_idle(self) -> None:
            self.processing_task = None

        def interrupt(self) -> bool:
            return False

    runtime = FakeRuntime()
    monkeypatch.setattr("faltoobot.chat.build_chat_runtime", lambda *args, **kwargs: runtime)

    async def fake_read_input(prompt: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("faltoobot.chat.read_input", fake_read_input)

    await run_chat(config=config)

    assert runtime.started
    assert runtime.closed


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
async def test_tree_opens_current_session_messages_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    console, output = runtime_console()
    opened: list[Path] = []
    monkeypatch.setattr("faltoobot.chat.open_in_default_editor", lambda path: opened.append(path))

    runtime = build_chat_runtime(console=console)
    await runtime.start()
    assert runtime.session is not None
    await runtime.submit("/tree")
    await runtime.close()

    assert opened == [runtime.session.messages_file]
    assert str(runtime.session.messages_file) in output.getvalue()


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
            {
                "type": "shell_call",
                "call_id": "call_1",
                "action": {"commands": ["pwd"]},
            },
            {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "Checking previous context."}],
            }
        ],
    )

    console, output = runtime_console()
    runtime = build_chat_runtime(console=console)
    await runtime.start()
    await runtime.close()

    text = output.getvalue()
    assert "tool> shell: pwd" in text
    assert "thinking> Checking previous context." in text
    assert "you> hello" in text
    assert "bot> world" in text


@pytest.mark.anyio
async def test_chat_shows_thinking_summary_for_live_reply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch, thinking="medium")
    console, output = runtime_console()

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "text": "done",
            "output_items": [
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "Planning the answer."}],
                }
            ],
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_stream_reply)

    runtime = build_chat_runtime(console=console)
    await runtime.start()
    await runtime.submit("hi")
    await runtime.wait_until_idle()
    await runtime.close()

    text = output.getvalue()
    assert "thinking> Planning the answer." in text
    assert "bot> done" in text


@pytest.mark.anyio
async def test_chat_renders_markdown_for_user_and_bot_messages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    console, output = runtime_console()

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "text": "**bold** answer",
            "output_items": [],
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_stream_reply)

    runtime = build_chat_runtime(console=console)
    await runtime.start()
    await runtime.submit("**bold** prompt")
    await runtime.wait_until_idle()
    await runtime.close()

    text = output.getvalue()
    assert "**bold**" not in text
    assert "bold answer" in text


@pytest.mark.anyio
async def test_chat_renders_markdown_for_thinking_messages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    console, output = runtime_console()

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "text": "done",
            "output_items": [
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "**bold** thinking"}],
                }
            ],
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_stream_reply)

    runtime = build_chat_runtime(console=console)
    await runtime.start()
    await runtime.submit("hi")
    await runtime.wait_until_idle()
    await runtime.close()

    text = output.getvalue()
    assert "**bold**" not in text
    assert "bold thinking" in text


@pytest.mark.anyio
async def test_chat_streams_bot_reply_live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    console, output = runtime_console()

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        await kwargs["on_text_delta"]("hel")
        await kwargs["on_text_delta"]("lo")
        return {
            "text": "hello",
            "output_items": [],
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_stream_reply)

    runtime = build_chat_runtime(console=console)
    await runtime.start()
    await runtime.submit("hi")
    await runtime.wait_until_idle()
    await runtime.close()

    text = output.getvalue()
    assert "bot> hello" in text
    assert text.count("bot> hello") == 1


@pytest.mark.anyio
async def test_chat_streams_inline_styles_in_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    output = StringIO()
    console = Console(file=output, force_terminal=True, color_system="truecolor", width=120)
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        await kwargs["on_text_delta"]("**bold** ")
        await kwargs["on_text_delta"]("hi")
        started.set()
        await release.wait()
        return {
            "text": "**bold** hi",
            "output_items": [],
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_stream_reply)

    runtime = build_chat_runtime(console=console)
    await runtime.start()
    await runtime.submit("hi")
    await started.wait()

    raw = output.getvalue()
    assert "\x1b[1A" not in raw
    assert "\x1b[" in raw
    assert "**bold** hi" in raw

    release.set()
    await runtime.wait_until_idle()
    await runtime.close()


@pytest.mark.anyio
async def test_chat_streams_thinking_live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    console, output = runtime_console()

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        await kwargs["on_reasoning_delta"]("plan")
        await kwargs["on_reasoning_done"]()
        return {
            "text": "hello",
            "output_items": [
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "plan"}],
                }
            ],
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_stream_reply)

    runtime = build_chat_runtime(console=console)
    await runtime.start()
    await runtime.submit("hi")
    await runtime.wait_until_idle()
    await runtime.close()

    text = output.getvalue()
    assert "thinking> plan" in text
    assert text.count("thinking> plan") == 1
    assert "bot> hello" in text


@pytest.mark.anyio
async def test_chat_shows_tool_calls_live_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    console, output = runtime_console()

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        tool_item = {
            "type": "shell_call",
            "call_id": "call_1",
            "action": {"commands": ["pwd"]},
        }
        await kwargs["on_output_item"](tool_item)
        return {
            "text": "done",
            "output_items": [tool_item],
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_stream_reply)

    runtime = build_chat_runtime(console=console)
    await runtime.start()
    await runtime.submit("hi")
    await runtime.wait_until_idle()
    await runtime.close()

    text = output.getvalue()
    assert "tool> shell: pwd" in text
    assert text.count("tool> shell: pwd") == 1


@pytest.mark.anyio
async def test_reset_creates_new_session_for_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = prepare_home(tmp_path, monkeypatch)
    console, output = runtime_console()
    config = build_config()
    original = cli_session(config.sessions_dir, "CLI original", workspace)
    original = add_turn(original, "user", "first")
    add_turn(original, "assistant", "reply")

    runtime = build_chat_runtime(console=console)
    await runtime.start()
    original_id = runtime.session.id if runtime.session else ""
    assert await runtime.submit("/reset")
    await runtime.close()

    assert runtime.session is not None
    assert runtime.session.id != original_id
    assert runtime.session.workspace == workspace
    assert runtime.session.messages == ()
    reloaded_original = existing_cli_session(config.sessions_dir, workspace)
    assert reloaded_original is not None
    assert reloaded_original.id == runtime.session.id
    assert original.messages_file.exists()
    assert "new session:" in output.getvalue()


@pytest.mark.anyio
async def test_chat_queues_messages_while_reply_is_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    console, output = runtime_console()
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

    runtime = build_chat_runtime(console=console)
    await runtime.start()
    assert await runtime.submit("first")
    await started.wait()
    assert await runtime.submit("second")
    assert prompts == ["first"]
    release.set()
    await runtime.wait_until_idle()
    await runtime.close()

    text = output.getvalue()
    assert prompts == ["first", "second"]
    assert "bot> reply:first" in text
    assert "bot> reply:second" in text


@pytest.mark.anyio
async def test_chat_can_interrupt_inflight_reply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    console, output = runtime_console()
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

    runtime = build_chat_runtime(console=console)
    await runtime.start()
    assert await runtime.submit("first")
    await started.wait()

    assert runtime.interrupt()
    await runtime.wait_until_idle()
    await runtime.close()

    text = output.getvalue()
    assert "bot> partial" in text
    assert "reply interrupted" in text
    assert "partial done" not in text
    assert runtime.session is not None
    assert [turn.role for turn in runtime.session.messages] == ["user"]
