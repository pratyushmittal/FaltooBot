from pathlib import Path

from faltoobot.chat.app import build_chat_app
from faltoobot.config import build_config


def write_config(home: Path) -> None:
    config_dir = home / ".faltoobot"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(
        "\n".join(
            [
                "# Faltoobot config",
                "",
                "[openai]",
                'api_key = "test-key"',
                'model = "gpt-5.4"',
                'thinking = "high"',
                'fast = false',
                "",
                "[bot]",
                "allow_groups = false",
                "allowed_chats = []",
                'system_prompt = "Test prompt."',
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )



def test_chat_theme_is_persisted_and_restored(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    write_config(home)
    monkeypatch.setenv("HOME", str(home))

    first_app = build_chat_app(config=build_config(), terminal_dark=False)
    first_app.theme = "dracula"

    second_app = build_chat_app(config=build_config(), terminal_dark=False)

    assert (home / ".faltoobot" / "chat-theme.txt").read_text(encoding="utf-8").strip() == "dracula"
    assert second_app.theme == "dracula"
