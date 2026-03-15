from pathlib import Path
from typing import Any

import pytest

from faltoobot.agent import reasoning_config, reply, stream_reply, system_instructions
from faltoobot.config import build_config
from faltoobot.store import create_cli_session


class FakeResponse:
    output_text = "ok"
    output: list[dict[str, Any]] = []
    usage = {"total_tokens": 1}


class FakeResponses:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(kwargs)
        return FakeResponse()

    def stream(self, **kwargs: Any) -> "FakeStreamManager":
        self.calls.append(kwargs)
        return FakeStreamManager()


class FakeClient:
    def __init__(self) -> None:
        self.responses = FakeResponses()


class FakeLoopResponses:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.count = 0

    async def create(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(kwargs)
        self.count += 1
        response = FakeResponse()
        if self.count == 1:
            response.output_text = ""
            response.output = [
                {
                    "type": "shell_call",
                    "call_id": "call_1",
                    "action": {"commands": ["pwd"], "max_output_length": 4000},
                }
            ]
        else:
            response.output_text = "done"
            response.output = []
        return response


class FakeLoopClient:
    def __init__(self) -> None:
        self.responses = FakeLoopResponses()


class FakeSanitizeResponses:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(kwargs)
        return FakeResponse()


class FakeSanitizeClient:
    def __init__(self) -> None:
        self.responses = FakeSanitizeResponses()


class FakeStreamEvent:
    def __init__(self, event_type: str, delta: str = "") -> None:
        self.type = event_type
        self.delta = delta


class FakeStreamManager:
    async def __aenter__(self) -> "FakeStreamManager":
        return self

    async def __aexit__(self, exc_type: object, exc: object, exc_tb: object) -> None:
        return None

    def __aiter__(self) -> "FakeStreamManager":
        self._events = iter(
            [
                FakeStreamEvent("response.reasoning_summary_text.delta", "plan"),
                FakeStreamEvent("response.reasoning_summary_text.done"),
                FakeStreamEvent("response.output_text.delta", "hel"),
                FakeStreamEvent("response.output_text.delta", "lo"),
            ]
        )
        return self

    async def __anext__(self) -> FakeStreamEvent:
        try:
            return next(self._events)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    async def get_final_response(self) -> FakeResponse:
        response = FakeResponse()
        response.output_text = "hello"
        return response


class FakeLoopStreamManager:
    def __init__(self, count: int) -> None:
        self.count = count

    async def __aenter__(self) -> "FakeLoopStreamManager":
        return self

    async def __aexit__(self, exc_type: object, exc: object, exc_tb: object) -> None:
        return None

    def __aiter__(self) -> "FakeLoopStreamManager":
        self._events = iter(())
        return self

    async def __anext__(self) -> FakeStreamEvent:
        raise StopAsyncIteration

    async def get_final_response(self) -> FakeResponse:
        response = FakeResponse()
        if self.count == 1:
            response.output_text = ""
            response.output = [
                {
                    "type": "shell_call",
                    "call_id": "call_1",
                    "action": {"commands": ["pwd"], "max_output_length": 4000},
                }
            ]
        else:
            response.output_text = "done"
            response.output = []
        return response


class FakeLoopStreamResponses:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.count = 0

    def stream(self, **kwargs: Any) -> FakeLoopStreamManager:
        self.calls.append(kwargs)
        self.count += 1
        return FakeLoopStreamManager(self.count)


class FakeLoopStreamClient:
    def __init__(self) -> None:
        self.responses = FakeLoopStreamResponses()


@pytest.mark.anyio
async def test_reply_includes_global_and_session_agents_in_instructions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = home / ".faltoobot"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    (root / "AGENTS.md").write_text("Global guardrails.", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("Session rules.", encoding="utf-8")

    config = build_config()
    session = create_cli_session(config.sessions_dir, "CLI test", workspace)
    client = FakeClient()

    result = await reply(client, config, session, [{"type": "message", "role": "user", "content": "hi"}])  # type: ignore[arg-type]

    assert result["text"] == "ok"
    instructions = client.responses.calls[0]["instructions"]
    assert result["instructions"] == instructions
    assert instructions == system_instructions(config, session)
    assert "Global AGENTS.md:\nGlobal guardrails." in instructions
    assert "Session AGENTS.md:\nSession rules." in instructions
    assert config.system_prompt in instructions


def test_reasoning_config_enables_auto_summaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    root = home / ".faltoobot"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))

    config = build_config()

    assert reasoning_config(config) == {
        "effort": config.openai_thinking,
        "summary": "auto",
    }


@pytest.mark.anyio
async def test_stream_reply_emits_text_deltas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = home / ".faltoobot"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))

    config = build_config()
    session = create_cli_session(config.sessions_dir, "CLI test", workspace)
    client = FakeClient()
    deltas: list[str] = []
    reasoning_deltas: list[str] = []
    reasoning_done: list[str] = []

    result = await stream_reply(
        client,
        config,
        session,
        [{"type": "message", "role": "user", "content": "hi"}],  # type: ignore[arg-type]
        on_text_delta=deltas.append,
        on_reasoning_delta=reasoning_deltas.append,
        on_reasoning_done=lambda: reasoning_done.append("done"),
    )

    assert deltas == ["hel", "lo"]
    assert reasoning_deltas == ["plan"]
    assert reasoning_done == ["done"]
    assert result["text"] == "hello"
    assert result["instructions"] == system_instructions(config, session)


@pytest.mark.anyio
async def test_reply_keeps_intermediate_tool_call_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = home / ".faltoobot"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))

    config = build_config()
    session = create_cli_session(config.sessions_dir, "CLI test", workspace)
    client = FakeLoopClient()

    result = await reply(client, config, session, [{"type": "message", "role": "user", "content": "hi"}])  # type: ignore[arg-type]

    assert result["text"] == "done"
    assert any(item.get("type") == "shell_call" for item in result["output_items"])
    assert any(item.get("type") == "shell_call_output" for item in result["output_items"])


@pytest.mark.anyio
async def test_reply_strips_parsed_arguments_from_replayed_tool_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = home / ".faltoobot"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))

    config = build_config()
    session = create_cli_session(config.sessions_dir, "CLI test", workspace)
    client = FakeSanitizeClient()

    result = await reply(
        client,
        config,
        session,
        [
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "skills",
                "arguments": '{"action":"list"}',
                "parsed_arguments": {"action": "list"},
            }
        ],
    )  # type: ignore[arg-type]

    assert result["text"] == "ok"
    sent_item = client.responses.calls[0]["input"][0]
    assert sent_item["arguments"] == '{"action":"list"}'
    assert "parsed_arguments" not in sent_item

@pytest.mark.anyio
async def test_stream_reply_emits_stream_end_snapshots_for_tool_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = home / ".faltoobot"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))

    config = build_config()
    session = create_cli_session(config.sessions_dir, "CLI test", workspace)
    client = FakeLoopStreamClient()
    snapshots: list[tuple[str, list[str]]] = []

    result = await stream_reply(
        client,
        config,
        session,
        [{"type": "message", "role": "user", "content": "hi"}],  # type: ignore[arg-type]
        on_stream_end=lambda items, text: snapshots.append((text, [item["type"] for item in items])),
    )

    assert snapshots == [
        ("", ["shell_call", "shell_call_output"]),
        ("done", ["shell_call", "shell_call_output"]),
    ]
    assert result["text"] == "done"


