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
