from pathlib import Path
from faltoobot import instructions
from faltoobot.config import Config


def test_get_system_instructions_skips_empty_agents_files(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(instructions.Path, "home", lambda: tmp_path / "home")
    config = Config(
        home=tmp_path,
        root=tmp_path / ".faltoobot",
        config_file=tmp_path / ".faltoobot" / "config.toml",
        log_file=tmp_path / ".faltoobot" / "faltoobot.log",
        sessions_dir=tmp_path / ".faltoobot" / "sessions",
        session_db=tmp_path / ".faltoobot" / "session.db",
        launch_agent=tmp_path / ".faltoobot" / "agent.plist",
        run_script=tmp_path / ".faltoobot" / "run.sh",
        openai_api_key="",
        openai_oauth="",
        openai_model="gpt-5.4",
        openai_thinking="high",
        openai_fast=False,
        openai_transcription_model="gpt-4o-transcribe",
        allow_group_chats=set(),
        allowed_chats=set(),
        bot_name="Faltoo",
        browser_binary="",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "AGENTS.md").write_text("\n\n", encoding="utf-8")

    result = instructions.get_system_instructions(config, "code@test", workspace)

    assert "You are Faltoo." in result
    assert "Session AGENTS.md" not in result
