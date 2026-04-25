from pathlib import Path

from faltoobot.config import Config, default_config, render_config
from faltoobot.migrate import (
    main,
    remove_session_last_used_files,
    update_default_openai_model,
)


def make_config(tmp_path: Path) -> Config:
    home = tmp_path / "home"
    root = home / ".faltoobot"
    return Config(
        home=home,
        root=root,
        config_file=root / "config.toml",
        log_file=root / "faltoobot.log",
        sessions_dir=root / "sessions",
        session_db=root / "session.db",
        launch_agent=home / "Library" / "LaunchAgents" / "com.faltoobot.agent.plist",
        run_script=root / "run.sh",
        openai_api_key="",
        openai_oauth="",
        openai_model="gpt-5.5",
        openai_thinking="high",
        openai_fast=False,
        openai_transcription_model="gpt-4o-transcribe",
        allow_group_chats=set(),
        allowed_chats=set(),
        bot_name="Faltoo",
        browser_binary="",
    )


def test_remove_session_last_used_files(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    stale = config.sessions_dir / "code@test" / "last_used"
    messages = config.sessions_dir / "code@test" / "named" / "messages.json"
    stale.parent.mkdir(parents=True)
    messages.parent.mkdir(parents=True)
    stale.write_text("old\n", encoding="utf-8")
    messages.write_text("{}\n", encoding="utf-8")

    changed = remove_session_last_used_files(config)

    assert changed
    assert not stale.exists()
    assert messages.exists()


def test_migrate_main_accepts_config(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    stale = config.sessions_dir / "code@test" / "last_used"
    stale.parent.mkdir(parents=True)
    stale.write_text("old\n", encoding="utf-8")

    changes = main(config)

    assert changes == ["migration:remove-session-last-used"]
    assert not stale.exists()


def test_migrate_main_returns_empty_when_clean(tmp_path: Path) -> None:
    config = make_config(tmp_path)

    assert main(config) == []


def test_update_default_openai_model_moves_old_default(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    data = default_config()
    data["openai"]["model"] = "gpt-5.4"
    config.config_file.parent.mkdir(parents=True)
    config.config_file.write_text(render_config(data), encoding="utf-8")

    changed = update_default_openai_model(config)

    assert changed
    assert 'model = "gpt-5.5"' in config.config_file.read_text(encoding="utf-8")


def test_update_default_openai_model_keeps_custom_model(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    data = default_config()
    data["openai"]["model"] = "gpt-5.2-codex"
    config.config_file.parent.mkdir(parents=True)
    config.config_file.write_text(render_config(data), encoding="utf-8")

    changed = update_default_openai_model(config)

    assert not changed
    assert 'model = "gpt-5.2-codex"' in config.config_file.read_text(encoding="utf-8")


def test_migrate_main_updates_default_openai_model(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    data = default_config()
    data["openai"]["model"] = "gpt-5.4"
    config.config_file.parent.mkdir(parents=True)
    config.config_file.write_text(render_config(data), encoding="utf-8")

    assert main(config) == ["migration:update-default-openai-model"]
