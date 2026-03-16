import json
import os
from pathlib import Path

import pytest

from faltoobot.chat import build_chat_runtime


def config_text(system_prompt: str) -> str:
    return "\n".join(
        [
            "# Faltoobot config",
            "",
            "[openai]",
            'api_key = ""',
            'model = "gpt-5.4"',
            'thinking = "high"',
            "",
            "[bot]",
            "allow_groups = false",
            "allowed_chats = []",
            f"system_prompt = {json.dumps(system_prompt)}",
            "",
        ]
    )


def session_payload(home: Path) -> dict[str, object]:
    messages_files = sorted((home / ".faltoobot" / "sessions").glob("*/messages.json"))
    assert len(messages_files) == 1
    return json.loads(messages_files[0].read_text(encoding="utf-8"))


async def run_chat_turn(
    home: Path,
    prompt: str,
    name: str | None = "E2E Chat",
) -> dict[str, object]:
    runtime = build_chat_runtime(name=name)
    await runtime.start()
    assert await runtime.submit(prompt)
    await runtime.close()
    return session_payload(home)


@pytest.mark.anyio
async def test_faltoochat_uses_env_api_key_and_persists_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY must be set to run this E2E test.")

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = home / ".faltoobot" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        config_text("Reply with exactly the requested text when asked to do so."),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(workspace)

    prompt = "Reply with exactly FALTOO_E2E_OK and nothing else."
    payload = await run_chat_turn(home, prompt)
    messages = payload["messages"]
    assert payload["name"] == "CLI E2E Chat"
    assert payload["workspace"] == str(workspace)
    assert isinstance(messages, list)
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["content"] == prompt
    assert messages[1]["content"] == "FALTOO_E2E_OK"
    assert messages[1]["items"]
    assert messages[1]["usage"]["total_tokens"] > 0
    assert "Reply with exactly the requested text when asked to do so." in messages[1].get("instructions", "")


@pytest.mark.anyio
async def test_faltoochat_runs_pwd_in_session_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY must be set to run this E2E test.")

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = home / ".faltoobot" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        config_text("If the user asks to run a shell command, use the shell tool and return the command output only."),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(workspace)

    prompt = "Run `pwd` in the shell tool and reply with only the output."
    payload = await run_chat_turn(home, prompt)
    messages = payload["messages"]
    assert isinstance(messages, list)
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["content"] == prompt
    assert payload["workspace"] == str(workspace)
    assert messages[1]["content"] == str(workspace)
    assert messages[1]["items"]
    assert messages[1]["usage"]["total_tokens"] > 0
    assert "instructions" not in messages[1] or isinstance(messages[1]["instructions"], str)


@pytest.mark.anyio
async def test_faltoochat_reuses_existing_session_for_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY must be set to run this E2E test.")

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = home / ".faltoobot" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        config_text("Reply with exactly the requested text when asked to do so."),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(workspace)

    first = await run_chat_turn(home, "Reply with exactly FIRST_RUN_OK and nothing else.", name=None)
    second = await run_chat_turn(home, "Reply with exactly SECOND_RUN_OK and nothing else.", name=None)

    first_messages = first["messages"]
    second_messages = second["messages"]
    assert first["id"] == second["id"]
    assert first["workspace"] == str(workspace)
    assert second["workspace"] == str(workspace)
    assert isinstance(first_messages, list)
    assert isinstance(second_messages, list)
    assert len(first_messages) == 2
    assert len(second_messages) == 4
    assert [message["content"] for message in second_messages] == [
        "Reply with exactly FIRST_RUN_OK and nothing else.",
        "FIRST_RUN_OK",
        "Reply with exactly SECOND_RUN_OK and nothing else.",
        "SECOND_RUN_OK",
    ]
    assert second_messages[1]["instructions"]
    assert "instructions" not in second_messages[3]
