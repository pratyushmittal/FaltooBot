import json
import os
from pathlib import Path

import pytest
from textual.widgets import Input

from faltoobot.chat import build_chat_app


def config_text() -> str:
    return "\n".join(
        [
            "# Faltoobot config",
            "",
            "[openai]",
            'api_key = ""',
            'model = "gpt-5.2"',
            "",
            "[bot]",
            "allow_groups = false",
            "allowed_chats = []",
            "max_history_messages = 12",
            'system_prompt = "Reply with exactly the requested text when asked to do so."',
            "",
        ]
    )


def session_payload(home: Path) -> dict[str, object]:
    messages_files = sorted((home / ".faltoobot" / "sessions").glob("*/messages.json"))
    assert len(messages_files) == 1
    return json.loads(messages_files[0].read_text(encoding="utf-8"))


@pytest.mark.anyio
async def test_faltoochat_uses_env_api_key_and_persists_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY must be set to run this E2E test.")

    home = tmp_path / "home"
    config_path = home / ".faltoobot" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config_text(), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    prompt = "Reply with exactly FALTOO_E2E_OK and nothing else."
    app = build_chat_app(name="E2E Chat")

    async with app.run_test() as pilot:
        input_widget = app.query_one(Input)
        input_widget.value = prompt
        input_widget.focus()
        await pilot.press("enter")

        for _ in range(30):
            payload = session_payload(home)
            messages = payload["messages"]
            if (
                isinstance(messages, list)
                and len(messages) == 2
                and messages[1]["content"] == "FALTOO_E2E_OK"
            ):
                break
            await pilot.pause(0.2)
        else:
            raise AssertionError("assistant response was not persisted")

        input_widget.value = "/exit"
        input_widget.focus()
        await pilot.press("enter")

    payload = session_payload(home)
    messages = payload["messages"]
    assert payload["name"] == "CLI E2E Chat"
    assert isinstance(messages, list)
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["content"] == prompt
    assert messages[1]["content"] == "FALTOO_E2E_OK"
    assert messages[1]["items"]
