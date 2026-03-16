import asyncio
import json
import threading
import time
from io import BytesIO, StringIO
from pathlib import Path

import pytest
from PIL import Image
from rich.console import Console
from rich.text import Text
from textual import events
from textual.widgets import Markdown as TextualMarkdown

from faltoobot.chat import (
    Composer,
    EntryBlock,
    LiveMarkdownBlock,
    SlashCommandItem,
    slash_query,
    slash_suggestions,
    QueuedPrompt,
    QueueItem,
    build_chat_app,
    build_chat_runtime,
    fitted_image_size,
    image_markdown,
    input_image_part,
    paste_image_text,
    queue_preview,
    rich_renderable,
    status_text,
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


def prepare_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, thinking: str = "none"
) -> Path:
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


def transcript_blocks(app: object) -> list[EntryBlock]:
    return [
        block for block in app.query_one("#transcript").children if isinstance(block, EntryBlock)
    ]  # type: ignore[attr-defined]


def live_markdown_blocks(app: object) -> list[LiveMarkdownBlock]:
    return [
        block
        for block in app.query_one("#transcript").children
        if isinstance(block, LiveMarkdownBlock)
    ]  # type: ignore[attr-defined]


def block_plain(block: EntryBlock) -> str:
    rendered = block.query_one("#body").render()
    return rendered.plain if isinstance(rendered, Text) else str(rendered)


def queue_texts(app: object) -> list[str]:
    return [item.content for item in app.query("#queue QueueItem")]  # type: ignore[attr-defined]


def queue_items(app: object) -> list[object]:
    return list(app.query("#queue QueueItem"))  # type: ignore[attr-defined]


def queue_paused(app: object) -> list[bool]:
    return [item.paused for item in app.query("#queue QueueItem")]  # type: ignore[attr-defined]


def queue_labels(app: object) -> list[str]:
    labels: list[str] = []
    for item in app.query("#queue QueueItem"):  # type: ignore[attr-defined]
        rendered = item.query_one(".queue-text").render()
        labels.append(rendered.plain if isinstance(rendered, Text) else str(rendered))
    return labels


def slash_command_texts(app: object) -> list[str]:
    return [item.command for item in app.query("#commands SlashCommandItem")]  # type: ignore[attr-defined]


def selected_queue_index(app: object) -> int | None:
    for index, item in enumerate(queue_items(app)):
        if item.has_class("-selected"):  # type: ignore[attr-defined]
            return index
    return None


def test_paste_image_text_ignores_long_non_path_text(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    text = "a" * 4096

    assert paste_image_text(text, workspace) == text


def test_paste_image_text_wraps_local_image_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    image = workspace / "cat.png"
    image.write_bytes(b"png")

    assert paste_image_text(str(image), workspace) == image_markdown(image.resolve())


def test_fitted_image_size_keeps_aspect_ratio() -> None:
    assert fitted_image_size(2000, 1000) == (1600, 800)
    assert fitted_image_size(1000, 2000) == (600, 1200)
    assert fitted_image_size(1200, 800) == (1200, 800)


@pytest.mark.anyio
async def test_input_image_part_resizes_large_local_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = prepare_home(tmp_path, monkeypatch)
    image = workspace / "large.png"
    Image.new("RGB", (2400, 1800), color="red").save(image)
    seen: list[tuple[str, tuple[int, int], str]] = []

    class FakeFiles:
        async def create(self, *, file: object, purpose: str) -> object:
            payload = file.read()  # type: ignore[attr-defined]
            with Image.open(BytesIO(payload)) as uploaded:
                seen.append((purpose, uploaded.size, Path(file.name).name))  # type: ignore[attr-defined]
            return type("Uploaded", (), {"id": "file_123"})()

    class FakeClient:
        files = FakeFiles()

    part = await input_image_part(FakeClient(), workspace, image.as_uri())

    assert part == {"type": "input_image", "file_id": "file_123", "detail": "auto"}
    assert seen == [("vision", (1600, 1200), "large-1600x1200.png")]


@pytest.mark.anyio
async def test_textual_app_action_paste_does_not_duplicate_text_paste(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    monkeypatch.setattr("faltoobot.chat.save_clipboard_image", lambda session: None)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        composer = app.query_one("#composer", Composer)
        composer.action_paste()
        await composer._on_paste(events.Paste("hello"))
        assert composer.text == "hello"


@pytest.mark.anyio
async def test_textual_app_action_paste_skips_following_text_event_for_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = prepare_home(tmp_path, monkeypatch)
    image = workspace / "clipboard.png"
    image.write_bytes(b"png")
    monkeypatch.setattr("faltoobot.chat.save_clipboard_image", lambda session: image)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        composer = app.query_one("#composer", Composer)
        composer.action_paste()
        await composer._on_paste(events.Paste("ignored"))
        assert composer.text == image_markdown(image)


@pytest.mark.anyio
async def test_textual_app_ctrl_v_inserts_clipboard_image_markdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = prepare_home(tmp_path, monkeypatch)
    image = workspace / "clipboard.png"
    image.write_bytes(b"png")
    monkeypatch.setattr("faltoobot.chat.save_clipboard_image", lambda session: image)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+v")
        await pilot.pause()
        assert app.query_one("#composer", Composer).text == image_markdown(image)


def test_status_text_shows_fast_suffix_when_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_home(tmp_path, monkeypatch)
    config = build_config()
    assert status_text(config) == "model: gpt-5.2  thinking: none"

    config.config_file.write_text(
        config.config_file.read_text(encoding="utf-8").replace("fast = false", "fast = true"),
        encoding="utf-8",
    )
    fast_config = build_config()
    assert status_text(fast_config) == "model: gpt-5.2 (fast)  thinking: none"


def test_queue_preview_flattens_multiline_content() -> None:
    assert queue_preview("first line\nsecond line\n\nthird") == "first line second line third"
    assert queue_preview("x" * 80) == "x" * 75


@pytest.mark.anyio
async def test_chat_submits_markdown_images_as_user_message_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = prepare_home(tmp_path, monkeypatch)
    image = workspace / "cat.png"
    image.write_bytes(b"png")
    seen: list[dict[str, object]] = []

    async def fake_input_image_part(*args: object, **kwargs: object) -> dict[str, object]:
        return {"type": "input_image", "file_id": "file_123", "detail": "auto"}

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        seen.extend(args[3])
        return {
            "text": "done",
            "output_items": [],
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.input_image_part", fake_input_image_part)
    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_stream_reply)

    runtime = build_chat_runtime()
    await runtime.start()
    await runtime.submit(f"What is this?\n{image_markdown(image)}")
    await runtime.wait_until_idle()
    text = transcript_text(runtime)
    await runtime.close()

    assert seen == [
        {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": "What is this?\n"},
                {"type": "input_image", "file_id": "file_123", "detail": "auto"},
            ],
        }
    ]
    assert "[image: cat.png]" in text


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
            {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "Checking context."}],
            },
        ],
    )

    runtime = build_chat_runtime()
    await runtime.start()
    text = transcript_text(runtime)
    await runtime.close()

    assert "shell" in text
    assert "\npwd" in text
    assert "Checking context." in text
    assert "hello" in text
    assert "world" in text


@pytest.mark.anyio
async def test_chat_updates_messages_file_after_tool_stream_ends(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    stream_saved = asyncio.Event()
    release = asyncio.Event()
    items = [
        {"type": "shell_call", "call_id": "call_1", "action": {"commands": ["pwd"]}},
        {
            "type": "shell_call_output",
            "call_id": "call_1",
            "status": "completed",
            "output": [{"stdout": "/tmp", "stderr": "", "outcome": {"type": "exit", "exit_code": 0}}],
        },
    ]

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        await kwargs["on_stream_end"](items, "")
        stream_saved.set()
        await release.wait()
        return {
            "text": "done",
            "output_items": items,
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_stream_reply)

    runtime = build_chat_runtime()
    await runtime.start()
    assert runtime.session is not None
    assert await runtime.submit("hi")
    await stream_saved.wait()
    payload = json.loads(runtime.session.messages_file.read_text(encoding="utf-8"))
    assert payload["messages"][-1]["role"] == "assistant"
    assert payload["messages"][-1]["content"] == ""
    assert [item["type"] for item in payload["messages"][-1]["items"]] == [
        "shell_call",
        "shell_call_output",
    ]
    release.set()
    await runtime.wait_until_idle()
    payload = json.loads(runtime.session.messages_file.read_text(encoding="utf-8"))
    await runtime.close()

    assert payload["messages"][-1]["content"] == "done"


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
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "Planning reply"}],
                },
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

    assert "hi" in text
    assert "Planning reply" in text
    assert text.count("shell") == 1
    assert "done" in text


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
    assert "reply:first" in text
    assert "reply:second" in text


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

    assert "partial" in text
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
async def test_textual_app_tab_does_nothing_without_queued_messages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a", "tab", "b")
        await pilot.pause()
        assert isinstance(app.focused, Composer)
        assert app.query_one("#composer", Composer).text == "ab"


@pytest.mark.anyio
async def test_textual_app_shows_slash_commands_after_typing_slash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("/")
        await pilot.pause()

        assert slash_command_texts(app) == ["/help", "/tree", "/reset", "/exit"]


@pytest.mark.anyio
async def test_textual_app_filters_slash_commands_while_typing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("/", "r", "e")
        await pilot.pause()

        assert slash_command_texts(app) == ["/reset"]


@pytest.mark.anyio
async def test_textual_app_clicking_slash_command_completes_composer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("/")
        await pilot.pause()

        app.on_slash_command_item_picked(SlashCommandItem.Picked("/reset"))  # type: ignore[attr-defined]
        await pilot.pause()

        assert app.query_one("#composer", Composer).text == "/reset"
        assert slash_command_texts(app) == ["/reset"]


@pytest.mark.anyio
async def test_textual_app_escape_hides_slash_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("/")
        await pilot.pause()
        assert slash_command_texts(app) == ["/help", "/tree", "/reset", "/exit"]

        await pilot.press("escape")
        await pilot.pause()
        assert slash_command_texts(app) == []

        await pilot.press("r")
        await pilot.pause()
        assert slash_command_texts(app) == ["/reset"]


@pytest.mark.anyio
async def test_textual_app_stays_responsive_while_shell_tool_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    shell_started = threading.Event()

    class FakeResponse:
        def __init__(self, *, text: str, output: list[dict[str, object]]) -> None:
            self.output_text = text
            self.output = output
            self.usage = None

    class FakeStreamManager:
        def __init__(self, count: int) -> None:
            self.count = count

        async def __aenter__(self) -> "FakeStreamManager":
            return self

        async def __aexit__(self, exc_type: object, exc: object, exc_tb: object) -> None:
            return None

        def __aiter__(self) -> "FakeStreamManager":
            return self

        async def __anext__(self) -> object:
            raise StopAsyncIteration

        async def get_final_response(self) -> FakeResponse:
            return (
                FakeResponse(
                    text="",
                    output=[
                        {
                            "type": "shell_call",
                            "call_id": "call_1",
                            "action": {"commands": ["pwd"]},
                        }
                    ],
                )
                if self.count == 1
                else FakeResponse(text="done", output=[])
            )

    class FakeResponses:
        def __init__(self) -> None:
            self.count = 0

        def stream(self, **kwargs: object) -> FakeStreamManager:
            self.count += 1
            return FakeStreamManager(self.count)

    class FakeClient:
        def __init__(self) -> None:
            self.responses = FakeResponses()

    def slow_shell_call(*args: object, **kwargs: object) -> dict[str, object]:
        shell_started.set()
        time.sleep(0.5)
        return {
            "type": "shell_call_output",
            "call_id": "call_1",
            "status": "completed",
            "output": [
                {"stdout": "/tmp", "stderr": "", "outcome": {"type": "exit", "exit_code": 0}}
            ],
        }

    monkeypatch.setattr("faltoobot.agent.run_shell_call", slow_shell_call)
    app = build_chat_app(client=FakeClient())

    async with app.run_test() as pilot:
        await pilot.pause()
        started_at = time.perf_counter()
        await pilot.press("h", "i", "enter")
        assert await asyncio.wait_for(asyncio.to_thread(shell_started.wait, 1.0), timeout=1.2)
        assert app.runtime.current_reply_task is not None
        assert time.perf_counter() - started_at < 0.45
        await pilot.press("x")
        await pilot.pause()
        assert app.query_one("#composer", Composer).text == "x"
        await app.runtime.wait_until_idle()


@pytest.mark.anyio
async def test_textual_app_shows_submitted_message_immediately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    release = asyncio.Event()

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        await release.wait()
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
        await pilot.pause()

        assert ("you", "ping") in entry_tuples(app.runtime)
        assert app.runtime.current_reply_task is not None

        release.set()
        await app.runtime.wait_until_idle()


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
async def test_textual_app_scrolls_to_new_submission_with_long_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = prepare_home(tmp_path, monkeypatch)
    config = build_config()
    session = cli_session(config.sessions_dir, "CLI history", workspace)
    for index in range(16):
        session = add_turn(session, "user", f"prompt {index}")
        session = add_turn(session, "assistant", f"reply {index} " * 12)

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "text": "done",
            "output_items": [],
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_stream_reply)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p", "u", "s", "h", "enter")
        await app.runtime.wait_until_idle()
        await pilot.pause()
        assert app.query_one("#transcript").is_vertical_scroll_end
        you_block = [block for block in transcript_blocks(app) if block.entry.kind == "you"][-1]
        assert "push" in block_plain(you_block)


@pytest.mark.anyio
async def test_textual_app_shows_user_prompt_and_stays_scrolled_to_bottom(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    release = asyncio.Event()

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        await release.wait()
        return {
            "text": "done",
            "output_items": [],
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_stream_reply)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p", "u", "s", "h", "enter")
        await pilot.pause()
        you_block = [block for block in transcript_blocks(app) if block.entry.kind == "you"][-1]
        assert "push" in block_plain(you_block)
        assert app.query_one("#transcript").is_vertical_scroll_end
        release.set()
        await app.runtime.wait_until_idle()


@pytest.mark.anyio
async def test_textual_app_renders_plain_user_and_bot_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
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
        blocks = transcript_blocks(app)
        you_block = next(block for block in blocks if block.entry.kind == "you")
        bot_block = next(
            block for block in blocks if block.entry.kind == "bot" and block.entry.content == "pong"
        )
        assert "ping" in block_plain(you_block)
        assert "pong" in block_plain(bot_block)


@pytest.mark.anyio
async def test_textual_app_starts_with_saved_tool_text_containing_markup_chars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = prepare_home(tmp_path, monkeypatch)
    config = build_config()
    session = cli_session(config.sessions_dir, "CLI history", workspace)
    session = add_turn(session, "user", "hi")
    add_turn(
        session,
        "assistant",
        "done",
        items=[
            {
                "type": "shell_call",
                "call_id": "call_1",
                "action": {"commands": ["echo [selected="]},
            }
        ],
    )

    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        assert any(
            block.entry.kind == "tool" and "echo [selected=" in block_plain(block)
            for block in transcript_blocks(app)
        )


@pytest.mark.anyio
async def test_textual_app_starts_scrolled_to_bottom_with_long_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = prepare_home(tmp_path, monkeypatch)
    config = build_config()
    session = cli_session(config.sessions_dir, "CLI history", workspace)
    for index in range(16):
        session = add_turn(session, "user", f"prompt {index}")
        session = add_turn(session, "assistant", f"reply {index} " * 12)

    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        assert app.query_one("#transcript").is_vertical_scroll_end


@pytest.mark.anyio
async def test_textual_app_does_not_pull_transcript_down_after_user_scrolls_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = prepare_home(tmp_path, monkeypatch)
    config = build_config()
    session = cli_session(config.sessions_dir, "CLI history", workspace)
    for index in range(16):
        session = add_turn(session, "user", f"prompt {index}")
        session = add_turn(session, "assistant", f"reply {index} " * 12)

    first_delta = asyncio.Event()
    continue_stream = asyncio.Event()
    release = asyncio.Event()

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        await kwargs["on_text_delta"]("partial")
        first_delta.set()
        await continue_stream.wait()
        await kwargs["on_text_delta"](" update")
        await release.wait()
        return {
            "text": "partial update",
            "output_items": [],
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_stream_reply)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        transcript = app.query_one("#transcript")
        transcript.scroll_end(animate=False, immediate=True)
        await pilot.press("n", "e", "x", "t", "enter")
        await first_delta.wait()
        await pilot.pause()
        assert transcript.is_vertical_scroll_end

        transcript.scroll_to(y=0, animate=False, immediate=True)
        app.stop_following_transcript()  # type: ignore[attr-defined]
        await pilot.pause()
        assert not transcript.is_vertical_scroll_end

        continue_stream.set()
        await pilot.pause()
        assert not transcript.is_vertical_scroll_end

        release.set()
        await app.runtime.wait_until_idle()
        await pilot.pause()
        assert not transcript.is_vertical_scroll_end


@pytest.mark.anyio
async def test_textual_app_keeps_final_reply_visible_with_long_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = prepare_home(tmp_path, monkeypatch)
    config = build_config()
    session = cli_session(config.sessions_dir, "CLI history", workspace)
    for index in range(16):
        session = add_turn(session, "user", f"prompt {index}")
        session = add_turn(session, "assistant", f"reply {index} " * 12)

    release = asyncio.Event()

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        await kwargs["on_text_delta"]("partial")
        await release.wait()
        return {
            "text": "final answer line 1\nfinal answer line 2",
            "output_items": [],
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_stream_reply)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        transcript = app.query_one("#transcript")
        transcript.scroll_end(animate=False, immediate=True)
        await pilot.press("n", "e", "x", "t", "enter")
        await pilot.pause()
        release.set()
        await app.runtime.wait_until_idle()
        await pilot.pause()
        await pilot.pause()
        assert transcript.is_vertical_scroll_end
        assert any(
            block.entry.content == "final answer line 1\nfinal answer line 2"
            for block in transcript_blocks(app)
        )


@pytest.mark.anyio
async def test_textual_app_shows_completed_reply_without_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    release = asyncio.Event()

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        await release.wait()
        return {
            "text": "visible now",
            "output_items": [],
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_stream_reply)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("h", "i", "enter")
        release.set()
        await app.runtime.wait_until_idle()
        await pilot.pause()
        assert ("bot", "visible now") in entry_tuples(app.runtime)
        assert any(block.entry.content == "visible now" for block in transcript_blocks(app))


@pytest.mark.anyio
async def test_textual_app_shows_tool_details_while_streaming(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    release = asyncio.Event()

    class FakeItem:
        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload

        def to_dict(self) -> dict[str, object]:
            return self.payload

    class FakeResponse:
        def __init__(self, *, text: str, output: list[dict[str, object]]) -> None:
            self.output_text = text
            self.output = output
            self.usage = None

    class FakeEvent:
        def __init__(self, event_type: str, item: dict[str, object] | None = None) -> None:
            self.type = event_type
            self.item = FakeItem(item) if item is not None else None

    class FakeStreamManager:
        def __init__(self, count: int) -> None:
            self.count = count

        async def __aenter__(self) -> "FakeStreamManager":
            return self

        async def __aexit__(self, exc_type: object, exc: object, exc_tb: object) -> None:
            return None

        def __aiter__(self) -> "FakeStreamManager":
            events = [
                FakeEvent(
                    "response.output_item.done",
                    {
                        "type": "shell_call",
                        "call_id": "call_1",
                        "action": {"commands": ["pwd"]},
                    },
                )
            ]
            self._events = iter(events if self.count == 1 else [])
            return self

        async def __anext__(self) -> FakeEvent:
            try:
                return next(self._events)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

        async def get_final_response(self) -> FakeResponse:
            if self.count == 1:
                await release.wait()
                return FakeResponse(
                    text="",
                    output=[
                        {
                            "type": "shell_call",
                            "call_id": "call_1",
                            "action": {"commands": ["pwd"]},
                        }
                    ],
                )
            return FakeResponse(text="done", output=[])

    class FakeResponses:
        def __init__(self) -> None:
            self.count = 0

        def stream(self, **kwargs: object) -> FakeStreamManager:
            self.count += 1
            return FakeStreamManager(self.count)

    class FakeClient:
        def __init__(self) -> None:
            self.responses = FakeResponses()

    app = build_chat_app(client=FakeClient())

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("h", "i", "enter")
        await pilot.pause()
        assert any(entry.kind == "tool" and "pwd" in entry.content for entry in app.runtime.entries)
        release.set()
        await app.runtime.wait_until_idle()


@pytest.mark.anyio
async def test_textual_app_reconciles_partial_bot_stream_with_final_reply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        await kwargs["on_text_delta"]("partial")
        return {
            "text": "partial and complete",
            "output_items": [],
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_stream_reply)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("h", "i", "enter")
        await app.runtime.wait_until_idle()
        await pilot.pause()
        assert ("bot", "partial and complete") in entry_tuples(app.runtime)
        assert any(
            block.entry.content == "partial and complete" for block in transcript_blocks(app)
        )


@pytest.mark.anyio
async def test_textual_app_preserves_live_thinking_line_breaks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    release = asyncio.Event()

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        await kwargs["on_reasoning_delta"]("**Calculating a date**\n\nDetails here")
        await release.wait()
        return {
            "text": "done",
            "output_items": [],
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_stream_reply)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("h", "i", "enter")
        await pilot.pause()
        assert app.runtime.live_entry is not None
        assert app.runtime.live_entry.kind == "thinking"
        assert app.runtime.live_entry.content == "**Calculating a date**\n\nDetails here"
        assert any(
            block.entry.content == "**Calculating a date**\n\nDetails here"
            for block in live_markdown_blocks(app)
        )
        release.set()
        await app.runtime.wait_until_idle()


@pytest.mark.anyio
async def test_textual_app_updates_live_markdown_blocks_incrementally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    first_delta = asyncio.Event()
    second_delta = asyncio.Event()
    release = asyncio.Event()

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        await kwargs["on_reasoning_delta"]("**Planning**")
        first_delta.set()
        await second_delta.wait()
        await kwargs["on_reasoning_delta"]("\n\nDetails")
        await release.wait()
        return {
            "text": "done",
            "output_items": [],
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_stream_reply)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("h", "i", "enter")
        await first_delta.wait()
        await pilot.pause()
        live_block = live_markdown_blocks(app)[0]
        assert live_block.entry.kind == "thinking"
        assert live_block.entry.content == "**Planning**"
        second_delta.set()
        await pilot.pause()
        assert live_markdown_blocks(app)[0].entry.content == "**Planning**\n\nDetails"
        release.set()
        await app.runtime.wait_until_idle()


@pytest.mark.anyio
async def test_textual_app_commits_streamed_markdown_as_markdown_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        await kwargs["on_text_delta"]("**Bold**")
        return {
            "text": "**Bold**",
            "output_items": [],
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_stream_reply)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("h", "i", "enter")
        await app.runtime.wait_until_idle()
        await pilot.pause()

        assert not live_markdown_blocks(app)
        bot_blocks = [block for block in transcript_blocks(app) if block.entry.kind == "bot"]
        assert bot_blocks
        assert isinstance(bot_blocks[-1].query_one("#body"), TextualMarkdown)


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


@pytest.mark.anyio
async def test_textual_app_up_keeps_multiline_cursor_in_composer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        app.runtime.pending_prompts = [QueuedPrompt("one")]  # type: ignore[attr-defined]
        app.sync_view(force=True)  # type: ignore[attr-defined]
        await pilot.pause()
        await pilot.press("h", "i", "shift+enter", "t", "h", "e", "r", "e")
        composer = app.query_one("#composer", Composer)
        before = composer.cursor_location

        await pilot.press("up")
        await pilot.pause()

        assert selected_queue_index(app) is None
        assert composer.cursor_location[0] < before[0]
        assert composer.text == "hi\nthere"


@pytest.mark.anyio
async def test_textual_app_routes_queue_keys_from_composer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        app.runtime.pending_prompts = [QueuedPrompt("one"), QueuedPrompt("two")]  # type: ignore[attr-defined]
        app.sync_view(force=True)  # type: ignore[attr-defined]
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert selected_queue_index(app) == 1
        await pilot.press("enter")
        await pilot.pause()
        assert queue_texts(app) == ["one"]
        assert app.query_one("#composer", Composer).text == "two"


@pytest.mark.anyio
async def test_textual_app_enter_edits_clicked_queue_item(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        app.runtime.pending_prompts = [QueuedPrompt("one")]  # type: ignore[attr-defined]
        app.sync_view(force=True)  # type: ignore[attr-defined]
        app.on_queue_item_picked(QueueItem.Picked(0))  # type: ignore[attr-defined]
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert queue_texts(app) == []
        assert app.query_one("#composer", Composer).text == "one"


@pytest.mark.anyio
async def test_textual_app_uses_arrow_navigation_and_enter_for_queue_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    release = asyncio.Event()

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        if args[2].messages[-1].content == "first":
            await release.wait()
        return {
            "text": "done",
            "output_items": [],
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_stream_reply)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f", "i", "r", "s", "t", "enter")
        await pilot.pause()
        await pilot.press("s", "e", "c", "o", "n", "d", "enter")
        await pilot.press("t", "h", "i", "r", "d", "enter")
        await pilot.pause()
        assert queue_texts(app) == ["second", "third"]
        assert selected_queue_index(app) is None
        assert app.handle_composer_key("tab")  # type: ignore[attr-defined]
        await pilot.pause()
        assert selected_queue_index(app) == 1
        assert queue_labels(app) == ["☑︎ second", "☑︎ third"]
        assert app.handle_composer_key("up")  # type: ignore[attr-defined]
        await pilot.pause()
        assert selected_queue_index(app) == 0
        assert app.handle_composer_key("enter")  # type: ignore[attr-defined]
        await pilot.pause()
        assert queue_texts(app) == ["third"]
        assert app.query_one("#composer", Composer).text == "second"
        release.set()
        await pilot.pause()


@pytest.mark.anyio
async def test_textual_app_moves_queue_selection_without_rebuilding_transcript(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        app.runtime.entries.append(type(app.runtime.entries[0])("bot", "hello"))  # type: ignore[attr-defined]
        app.runtime.pending_prompts = [QueuedPrompt("one"), QueuedPrompt("two")]  # type: ignore[attr-defined]
        app.sync_view(force=True)  # type: ignore[attr-defined]
        await pilot.pause()

        before = list(app.query_one("#transcript").children)
        assert app.handle_composer_key("tab")  # type: ignore[attr-defined]
        await pilot.pause()
        after = list(app.query_one("#transcript").children)

        assert before == after
        assert selected_queue_index(app) == 1


@pytest.mark.anyio
async def test_textual_app_tab_moves_between_queue_and_composer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        app.runtime.pending_prompts = [QueuedPrompt("one")]  # type: ignore[attr-defined]
        app.sync_view(force=True)  # type: ignore[attr-defined]
        await pilot.pause()
        assert selected_queue_index(app) is None
        assert app.handle_composer_key("tab")  # type: ignore[attr-defined]
        await pilot.pause()
        assert selected_queue_index(app) == 0
        assert app.handle_composer_key("tab")  # type: ignore[attr-defined]
        await pilot.pause()
        assert selected_queue_index(app) is None
        assert isinstance(app.focused, Composer)


@pytest.mark.anyio
async def test_textual_app_reorders_queue_with_shift_arrows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    release = asyncio.Event()

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        if args[2].messages[-1].content == "first":
            await release.wait()
        return {
            "text": "done",
            "output_items": [],
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_stream_reply)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f", "i", "r", "s", "t", "enter")
        await pilot.pause()
        await pilot.press("o", "n", "e", "enter")
        await pilot.press("t", "w", "o", "enter")
        await pilot.pause()
        assert app.handle_composer_key("tab")  # type: ignore[attr-defined]
        await pilot.pause()
        assert selected_queue_index(app) == 1
        assert app.handle_composer_key("shift+up")  # type: ignore[attr-defined]
        await pilot.pause()
        assert queue_texts(app) == ["two", "one"]
        assert selected_queue_index(app) == 0
        release.set()
        await app.runtime.wait_until_idle()


@pytest.mark.anyio
async def test_textual_app_space_toggles_queue_pause_and_tab_returns_to_composer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    monkeypatch.setattr("faltoobot.chat.ChatRuntime.ensure_processing", lambda self: None)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        app.runtime.pending_prompts = [QueuedPrompt("one")]  # type: ignore[attr-defined]
        app.sync_view(force=True)  # type: ignore[attr-defined]
        await pilot.pause()
        assert app.handle_composer_key("tab")  # type: ignore[attr-defined]
        await pilot.pause()
        assert app.handle_composer_key("space")  # type: ignore[attr-defined]
        await pilot.pause()
        assert queue_paused(app) == [True]
        assert queue_labels(app) == ["□ one"]
        assert app.handle_composer_key("tab")  # type: ignore[attr-defined]
        await pilot.pause()
        assert selected_queue_index(app) is None
        assert queue_paused(app) == [True]
        assert queue_labels(app) == ["□ one"]


@pytest.mark.anyio
async def test_textual_app_delete_removes_selected_queue_item(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        app.runtime.pending_prompts = [QueuedPrompt("one"), QueuedPrompt("two")]  # type: ignore[attr-defined]
        app.sync_view(force=True)  # type: ignore[attr-defined]
        await pilot.pause()
        assert app.handle_composer_key("tab")  # type: ignore[attr-defined]
        await pilot.pause()
        assert selected_queue_index(app) == 1
        assert app.handle_composer_key("delete")  # type: ignore[attr-defined]
        await pilot.pause()
        assert queue_texts(app) == ["one"]
        assert selected_queue_index(app) == 0


@pytest.mark.anyio
async def test_textual_app_paused_queue_does_not_auto_submit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    release = asyncio.Event()
    prompts: list[str] = []

    async def fake_stream_reply(*args: object, **kwargs: object) -> dict[str, object]:
        prompt = args[2].messages[-1].content
        prompts.append(prompt)
        if prompt == "first":
            await release.wait()
        return {
            "text": f"reply:{prompt}",
            "output_items": [],
            "usage": None,
            "instructions": "test instructions",
        }

    monkeypatch.setattr("faltoobot.chat.stream_reply", fake_stream_reply)
    app = build_chat_app()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f", "i", "r", "s", "t", "enter")
        await pilot.pause()
        await pilot.press("s", "e", "c", "o", "n", "d", "enter")
        await pilot.press("t", "h", "i", "r", "d", "enter")
        await pilot.pause()
        assert app.handle_composer_key("tab")  # type: ignore[attr-defined]
        assert app.handle_composer_key("up")  # type: ignore[attr-defined]
        assert app.handle_composer_key("space")  # type: ignore[attr-defined]
        await pilot.pause()
        assert queue_paused(app) == [True, False]
        assert queue_labels(app) == ["□ second", "☑︎ third"]
        release.set()
        await app.runtime.wait_until_idle()
        await pilot.pause()
        assert prompts == ["first", "third"]
        assert app.runtime.queued_prompts() == ("second",)
        assert app.runtime.queued_prompt_items()[0].paused is True


@pytest.mark.anyio
async def test_textual_app_restores_saved_queue_as_paused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_home(tmp_path, monkeypatch)
    runtime = build_chat_runtime()
    await runtime.start()
    runtime.enqueue_prompt("one")
    runtime.enqueue_prompt("two")
    assert runtime.session is not None
    messages_file = runtime.session.messages_file
    await runtime.close()

    payload = json.loads(messages_file.read_text(encoding="utf-8"))
    assert [item["content"] for item in payload["queued_prompts"]] == ["one", "two"]

    app = build_chat_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert queue_texts(app) == ["one", "two"]
        assert queue_paused(app) == [True, True]
        assert app.runtime.current_reply_task is None

    payload = json.loads(messages_file.read_text(encoding="utf-8"))
    assert [item["paused"] for item in payload["queued_prompts"]] == [True, True]
