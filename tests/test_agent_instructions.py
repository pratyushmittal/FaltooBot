from pathlib import Path
from typing import Any

import pytest

from faltoobot.agent import reply, system_instructions
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


class FakeClient:
    def __init__(self) -> None:
        self.responses = FakeResponses()


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
    assert instructions == system_instructions(config, session)
    assert "Global AGENTS.md:\nGlobal guardrails." in instructions
    assert "Session AGENTS.md:\nSession rules." in instructions
    assert config.system_prompt in instructions
