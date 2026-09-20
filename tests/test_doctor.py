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


def test_inspect_cron_health_reports_missing_venv_stale_home_and_logs(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    workdir = tmp_path / "job"
    workdir.mkdir()
    script = workdir / "monitor.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'PYTHON_BIN="${PYTHON_BIN:-$PWD/.venv/bin/python}"\n'
        "FALTOOBOT_BIN=/home/exedev/.local/bin/faltoobot\n"
        "echo run\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    log_dir = workdir / "logs"
    log_dir.mkdir()
    log_file = log_dir / "cron.log"
    log_file.write_text(
        "Missing Python interpreter at /tmp/job/.venv/bin/python\n"
        "Traceback (most recent call last):\n",
        encoding="utf-8",
    )

    crontab = f"17 * * * * cd {workdir} && ./monitor.sh >> logs/cron.log 2>&1\n"

    rendered = [
        issue.render()
        for issue in doctor.inspect_cron_health(config, crontab_text=crontab)
    ]

    assert any("missing local venv interpreter" in item for item in rendered)
    assert any(
        "references another home directory: /home/exedev/" in item for item in rendered
    )
    assert any("missing interpreter/path" in item for item in rendered)
    assert any("python traceback" in item for item in rendered)


def test_inspect_cron_health_reports_missing_workdir_and_script(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    workdir = tmp_path / "job"
    workdir.mkdir()
    crontab = "\n".join(
        [
            f"1 * * * * cd {tmp_path / 'missing'} && ./run.sh",
            f"2 * * * * cd {workdir} && ./missing.sh",
        ]
    )

    rendered = [
        issue.render()
        for issue in doctor.inspect_cron_health(config, crontab_text=crontab)
    ]

    assert any("working directory missing" in item for item in rendered)
    assert any("script missing" in item for item in rendered)


def test_summarize_session_health_counts_large_incomplete_and_unreadable(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    sessions_root = config.sessions_dir

    complete = sessions_root / "code@test" / "complete" / "messages.json"
    complete.parent.mkdir(parents=True)
    complete.write_text(
        '{"messages":[{"type":"message","role":"user","content":"hi"},'
        '{"type":"message","role":"assistant","content":"hello"}]}\n',
        encoding="utf-8",
    )

    incomplete = sessions_root / "sub-agent@test" / "incomplete" / "messages.json"
    incomplete.parent.mkdir(parents=True)
    incomplete.write_text(
        '{"messages":[{"type":"message","role":"user","content":"run"},'
        '{"type":"function_call","call_id":"call_1","name":"tool","arguments":"{}"},'
        '{"type":"function_call_output","call_id":"call_1","output":"ok"}]}\n',
        encoding="utf-8",
    )

    large = sessions_root / "code@test" / "large" / "messages.json"
    large.parent.mkdir(parents=True)
    large.write_text(
        '{"messages":[{"type":"message","role":"user","content":"big"},'
        '{"type":"message","role":"assistant","content":"done"}],'
        f'"padding":"{"x" * 200}"}}\n',
        encoding="utf-8",
    )

    corrupt = sessions_root / "code@test" / "corrupt" / "messages.json"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("{not-json\n", encoding="utf-8")

    workspace_artifact = (
        sessions_root / "code@test" / "complete" / "workspace" / "artifact.bin"
    )
    workspace_artifact.parent.mkdir(parents=True)
    workspace_artifact.write_bytes(b"artifact")

    archive = config.root / "sessions.tar.gz"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(b"backup")

    expected_histories = 4
    expected_largest_messages = 3
    summary = doctor.summarize_session_health(
        config,
        large_history_bytes=250,
        large_history_messages=expected_largest_messages,
    )

    assert summary.histories == expected_histories
    assert summary.large_histories == 1
    assert summary.long_histories == 1
    assert summary.largest_messages == expected_largest_messages
    assert summary.incomplete_histories == 1
    assert summary.unreadable_histories == 1
    assert summary.total_bytes >= complete.stat().st_size + incomplete.stat().st_size
    assert summary.largest_bytes == large.stat().st_size
    assert summary.session_tree_bytes >= summary.total_bytes
    assert summary.workspace_bytes == len(b"artifact")
    assert summary.archive_bytes == len(b"backup")
    assert summary.has_issues is True

    assert doctor.format_session_health_summary(summary) == [
        f"doctor:session-histories={expected_histories}",
        f"doctor:session-history-bytes={summary.total_bytes}",
        f"doctor:largest-session-messages={expected_largest_messages}",
        f"doctor:session-tree-bytes={summary.session_tree_bytes}",
        f"doctor:session-workspace-bytes={len(b'artifact')}",
        f"doctor:session-archive-bytes={len(b'backup')}",
        "doctor:large-session-histories=1",
        "doctor:long-session-histories=1",
        "doctor:incomplete-session-histories=1",
        "doctor:unreadable-session-histories=1",
    ]
