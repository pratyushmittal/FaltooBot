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


def test_inspect_cron_health_reports_stale_paths_broken_venv_and_logs(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    workdir = config.root / "sessions" / "chat" / "session" / "workspace"
    workdir.mkdir(parents=True)
    script = workdir / "watch.sh"
    script.write_text(
        """#!/usr/bin/env bash
PYTHON_BIN="${PYTHON_BIN:-$BASE_DIR/.venv/bin/python}"
FALTOOBOT_BIN="${FALTOOBOT_BIN:-/home/exedev/.local/bin/faltoobot}"
"$PYTHON_BIN" watch.py
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    log_dir = workdir / ".watch_logs"
    log_dir.mkdir()
    (log_dir / "cron.log").write_text(
        "Missing Python interpreter at .venv/bin/python\n"
        "RuntimeError: FaltooBot browser did not become ready on CDP port 9222\n",
        encoding="utf-8",
    )
    crontab_text = f"17 * * * * cd {workdir} && ./watch.sh >> .watch_logs/cron.log 2>&1\n"

    issues = [issue.render() for issue in doctor.inspect_cron_health(config, crontab_text=crontab_text)]

    assert any("another home directory: /home/exedev/" in issue for issue in issues)
    assert any("broken local venv interpreter" in issue for issue in issues)
    assert any("missing interpreter/path" in issue for issue in issues)
    assert any("browser startup failure" in issue for issue in issues)


def test_inspect_cron_health_reports_missing_cron_targets(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    missing_workdir = config.root / "missing-workdir"
    workdir = config.root / "workspace"
    workdir.mkdir(parents=True)
    crontab_text = (
        f"0 * * * * cd {missing_workdir} && ./watch.sh\n"
        f"1 * * * * cd {workdir} && ./missing.sh\n"
    )

    issues = [issue.render() for issue in doctor.inspect_cron_health(config, crontab_text=crontab_text)]

    assert any("working directory is missing" in issue for issue in issues)
    assert any("script is missing" in issue for issue in issues)
