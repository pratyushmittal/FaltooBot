import os
from pathlib import Path

from faltoobot import doctor
from faltoobot.config import Config


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


def test_ensure_function_call_outputs_repairs_dangling_and_null_outputs() -> None:
    history: doctor.MessageHistory = [
        {"type": "message", "role": "user", "content": "old"},
        {
            "type": "function_call",
            "id": "fc_missing",
            "call_id": "call_missing",
            "name": "greet",
            "arguments": '{"name":"Faltoo"}',
        },
        {"type": "message", "role": "user", "content": "next"},
        {
            "type": "function_call",
            "id": "fc_null",
            "call_id": "call_null",
            "name": "greet",
            "arguments": '{"name":"Bot"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_null",
            "output": None,
        },
    ]

    assert doctor.ensure_function_call_outputs(history) is True

    assert history == [
        {"type": "message", "role": "user", "content": "old"},
        {
            "type": "function_call",
            "id": "fc_missing",
            "call_id": "call_missing",
            "name": "greet",
            "arguments": '{"name":"Faltoo"}',
        },
        {
            "id": "fco_call_missing",
            "type": "function_call_output",
            "call_id": "call_missing",
            "output": doctor.MISSING_FUNCTION_CALL_OUTPUT,
            "status": "completed",
        },
        {"type": "message", "role": "user", "content": "next"},
        {
            "type": "function_call",
            "id": "fc_null",
            "call_id": "call_null",
            "name": "greet",
            "arguments": '{"name":"Bot"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_null",
            "output": doctor.MISSING_FUNCTION_CALL_OUTPUT,
            "status": "completed",
        },
    ]
    assert doctor.ensure_function_call_outputs(history) is False


def test_heal_last_used_files_uses_existing_mtime_convention(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    chat_root = config.sessions_dir / "code@test"
    broken = chat_root / "broken" / "messages.json"
    latest = chat_root / "latest" / "messages.json"
    broken.parent.mkdir(parents=True)
    latest.parent.mkdir(parents=True)
    broken.write_text(
        '{"messages":[{"type":"function_call","call_id":"call_1"}]}\n',
        encoding="utf-8",
    )
    latest.write_text('{"messages":[]}\n', encoding="utf-8")
    os.utime(broken, (100, 100))
    os.utime(latest, (200, 200))

    assert doctor.main(config) == [
        "doctor:heal-last-used",
        "doctor:heal-function-call-outputs",
    ]

    assert (chat_root / "last_used").read_text(encoding="utf-8") == "latest\n"


def test_heal_function_call_outputs_repairs_session_histories(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    messages_path = config.sessions_dir / "code@test" / "session-1" / "messages.json"
    messages_path.parent.mkdir(parents=True)
    messages_path.write_text(
        """{
  "messages": [
    {"type": "message", "role": "user", "content": "run"},
    {"type": "function_call", "call_id": "call_1", "name": "tool", "arguments": "{}"}
  ]
}
""",
        encoding="utf-8",
    )

    old_mtime = 100
    os.utime(messages_path, (old_mtime, old_mtime))

    assert doctor.heal_function_call_outputs(config) is True

    assert messages_path.stat().st_mtime == old_mtime
    text = messages_path.read_text(encoding="utf-8")
    assert '"type": "function_call_output"' in text
    assert '"call_id": "call_1"' in text
    assert "Tool call failed before output was saved." in text


def test_main_returns_doctor_changes(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    messages_path = config.sessions_dir / "code@test" / "session-1" / "messages.json"
    messages_path.parent.mkdir(parents=True)
    messages_path.write_text(
        '{"messages":[{"type":"function_call","call_id":"call_1"}]}\n',
        encoding="utf-8",
    )

    assert doctor.main(config) == [
        "doctor:heal-last-used",
        "doctor:heal-function-call-outputs",
    ]
