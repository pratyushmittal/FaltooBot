import asyncio
import json
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from faltoobot.chat import build_chat_runtime, prompt_bindings, prompt_toolbar, run_chat
from faltoobot.config import build_config
from faltoobot.store import add_turn, cli_session


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

    async def fake_reply(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "text": "done",
            "output_items": [
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "Planning the answer."}],
                }
            ],
            "usage": None,
        }

    monkeypatch.setattr("faltoobot.chat.reply", fake_reply)

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
        return {"text": f"reply:{prompts[-1]}", "output_items": [], "usage": None}

    monkeypatch.setattr("faltoobot.chat.reply", fake_reply)

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
