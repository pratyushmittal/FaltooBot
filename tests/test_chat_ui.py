import asyncio
import json
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from faltoobot.chat import build_chat_runtime, prompt_bindings, prompt_toolbar, run_chat
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


def test_chat_shows_model_and_thinking_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_home(tmp_path, monkeypatch, thinking="medium")
    config = build_config()

    toolbar = prompt_toolbar(config)
    text = "".join(part for _style, part in toolbar)

    assert f"model: {config.openai_model}" in text
    assert f"thinking: {config.openai_thinking}" in text
    assert "Ctrl+J newline" in text
    assert "Ctrl+Q interrupt" in text


def test_prompt_bindings_build_successfully() -> None:
    bindings = prompt_bindings()
    keys = {tuple(binding.keys) for binding in bindings.bindings}

    assert any(len(key) == 1 and str(key[0]) in {"Keys.ControlM", "Keys.Enter"} for key in keys)
    assert any(len(key) == 1 and str(key[0]) == "Keys.ControlJ" for key in keys)
    assert any(
        len(key) == 2
        and str(key[0]) == "Keys.Escape"
        and str(key[1]) in {"Keys.ControlM", "Keys.Enter"}
        for key in keys
    )
    assert any(len(key) == 1 and str(key[0]) == "Keys.ControlQ" for key in keys)


@pytest.mark.anyio
async def test_run_chat_passes_erase_when_done_to_prompt_session_constructor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    config = build_config()
    calls: list[dict[str, object]] = []

    class FakePromptSession:
        def __init__(self, *args: object, **kwargs: object) -> None:
            calls.append({"init": kwargs})

        async def prompt_async(self, *args: object, **kwargs: object) -> str:
            calls.append({"prompt_async": kwargs})
            raise EOFError

    class FakeRuntime:
        def __init__(self) -> None:
            self.config = config
            self.console = Console(file=StringIO(), force_terminal=False, width=80)
            self.started = False
            self.closed = False

        async def start(self) -> None:
            self.started = True

        async def close(self) -> None:
            self.closed = True

        def interrupt(self) -> bool:
            return False

    runtime = FakeRuntime()
    monkeypatch.setattr("faltoobot.chat.PromptSession", FakePromptSession)
    monkeypatch.setattr("faltoobot.chat.build_chat_runtime", lambda *args, **kwargs: runtime)

    await run_chat(config=config)

    assert runtime.started
    assert runtime.closed
    assert calls[0]["init"] == {"erase_when_done": True}
    assert "erase_when_done" not in calls[1]["prompt_async"]


@pytest.mark.anyio
async def test_run_chat_uses_prompt_toolkit_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    config = build_config()
    printed: list[str] = []

    class FakePromptSession:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def prompt_async(self, *args: object, **kwargs: object) -> str:
            raise EOFError

    def fake_print_formatted_text(*values: object, **kwargs: object) -> None:
        printed.extend(text for value in values for _style, text in value)

    monkeypatch.setattr("faltoobot.chat.PromptSession", FakePromptSession)
    monkeypatch.setattr("faltoobot.chat.print_formatted_text", fake_print_formatted_text)

    await run_chat(config=config)

    assert " faltoochat " in printed


@pytest.mark.anyio
async def test_run_chat_replays_existing_history_with_newlines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = prepare_home(tmp_path, monkeypatch)
    config = build_config()
    session = cli_session(config.sessions_dir, "CLI test", workspace)
    add_turn(session, "user", "hello")
    printed: list[tuple[str, str]] = []

    class FakePromptSession:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def prompt_async(self, *args: object, **kwargs: object) -> str:
            raise EOFError

    def fake_print_formatted_text(*values: object, **kwargs: object) -> None:
        printed.append(("".join(str(value) for value in values), kwargs.get("end", "\n")))

    monkeypatch.setattr("faltoobot.chat.PromptSession", FakePromptSession)
    monkeypatch.setattr("faltoobot.chat.print_formatted_text", fake_print_formatted_text)
    monkeypatch.setattr(
        "faltoobot.chat.render_ansi",
        lambda kind, content: f"{kind}> {content}",
    )

    await run_chat(config=config)

    assert any("you> hello" in text and end == "\n" for text, end in printed)


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
    assert text.count("you> hi") == 1
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
    assert "bold prompt" in text
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
        callback = kwargs["on_text_delta"]
        await callback("hel")
        await callback("lo")
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
async def test_run_chat_streams_while_prompt_is_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    config = build_config()
    streamed = asyncio.Event()
    printed: list[tuple[str, str]] = []
    run_in_terminal_calls: list[int] = []

    class FakePromptSession:
        prompts = 0

        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def prompt_async(self, *args: object, **kwargs: object) -> str:
            type(self).prompts += 1
            if type(self).prompts == 1:
                return "hi"
            await streamed.wait()
            raise EOFError

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        callback = kwargs["on_text_delta"]
        await callback("hel")
        await callback("lo")
        streamed.set()
        return {
            "text": "hello",
            "output_items": [],
            "usage": None,
            "instructions": "test instructions",
        }

    async def fake_run_in_terminal(func: object, **kwargs: object) -> None:
        del kwargs
        run_in_terminal_calls.append(1)
        func()  # type: ignore[operator]

    def fake_print_formatted_text(*values: object, **kwargs: object) -> None:
        printed.append(("".join(str(value) for value in values), kwargs.get("end", "\n")))

    monkeypatch.setattr("faltoobot.chat.PromptSession", FakePromptSession)
    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_stream_reply)
    monkeypatch.setattr("faltoobot.chat.run_in_terminal", fake_run_in_terminal)
    monkeypatch.setattr("faltoobot.chat.print_formatted_text", fake_print_formatted_text)

    await run_chat(config=config)

    assert len(run_in_terminal_calls) >= 3
    assert any("hel" in text and end == "" for text, end in printed)
    assert any("lo" in text and end == "" for text, end in printed)


@pytest.mark.anyio
async def test_command_is_printed_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    console, output = runtime_console()
    opened: list[Path] = []
    monkeypatch.setattr("faltoobot.chat.open_in_default_editor", lambda path: opened.append(path))

    runtime = build_chat_runtime(console=console)
    await runtime.start()
    await runtime.submit("/tree")
    await runtime.close()

    text = output.getvalue()
    assert text.count("you> /tree") == 1


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
    assert text.count("you> first") == 1
    assert text.count("you> second") == 1
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
        callback = kwargs["on_text_delta"]
        await callback("partial")
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
