import json
import os
import subprocess
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


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


def test_faltoochat_uses_env_api_key_and_persists_session(tmp_path: Path) -> None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY must be set to run this E2E test.")

    home = tmp_path / "home"
    config_path = home / ".faltoobot" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config_text(), encoding="utf-8")

    prompt = "Reply with exactly FALTOO_E2E_OK and nothing else."
    result = subprocess.run(
        ["uv", "run", "faltoochat", "--name", "E2E Chat"],
        input=f"{prompt}\n/exit\n",
        text=True,
        capture_output=True,
        cwd=repo_root(),
        env={**os.environ, "HOME": str(home)},
        timeout=120,
        check=True,
    )

    assert "bot> FALTOO_E2E_OK" in result.stdout

    sessions_dir = home / ".faltoobot" / "sessions"
    messages_files = sorted(sessions_dir.glob("*/messages.json"))
    assert len(messages_files) == 1

    payload = json.loads(messages_files[0].read_text(encoding="utf-8"))
    assert payload["name"] == "CLI E2E Chat"
    assert [message["role"] for message in payload["messages"]] == ["user", "assistant"]
    assert payload["messages"][0]["content"] == prompt
    assert payload["messages"][1]["content"] == "FALTOO_E2E_OK"
    assert payload["messages"][1]["items"]
