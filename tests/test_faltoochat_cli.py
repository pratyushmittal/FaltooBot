import sys
from pathlib import Path

from faltoobot.faltoochat import app as chat_app


def test_faltoochat_main_runs_one_shot(tmp_path: Path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "workspace"
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "faltoochat",
            "List new emails",
            "--workspace",
            str(workspace),
            "--new-session",
        ],
    )
    monkeypatch.setattr(
        chat_app.sessions,
        "get_session",
        lambda *, chat_key, session_id=None, workspace=None: (
            seen.update(
                {"chat_key": chat_key, "session_id": session_id, "workspace": workspace}
            )
            or (chat_key, "session-1")
        ),
    )

    async def fake_run_one_shot(session, prompt: str) -> str:
        seen["session"] = session
        seen["prompt"] = prompt
        return "There are 2 new emails."

    monkeypatch.setattr(chat_app, "_run_one_shot", fake_run_one_shot)

    result = chat_app.main()

    assert result == 0
    assert workspace.exists()
    assert seen["prompt"] == "List new emails"
    assert str(seen["chat_key"]).startswith("sub-agent@")
    assert capsys.readouterr().out.strip() == "There are 2 new emails."


def test_faltoochat_one_shot_notify_enqueues_without_printing_answer(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from types import SimpleNamespace

    workspace = tmp_path / "workspace"
    seen: dict[str, object] = {}
    enqueued: list[dict[str, object]] = []

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "faltoochat",
            "List new emails",
            "--workspace",
            str(workspace),
            "--new-session",
            "--notify=code@main",
        ],
    )
    monkeypatch.setattr(
        chat_app.sessions,
        "get_session",
        lambda *, chat_key, session_id=None, workspace=None: (
            seen.update({"session_id": session_id})
            or SimpleNamespace(chat_key=chat_key, session_id="session-1")
        ),
    )

    async def fake_run_one_shot(session, prompt: str) -> str:
        seen["prompt"] = prompt
        return "There are 2 new emails."

    monkeypatch.setattr(chat_app, "_run_one_shot", fake_run_one_shot)
    monkeypatch.setattr(
        chat_app.notify_queue,
        "enqueue_notification",
        lambda chat_key, message, **kwargs: (
            enqueued.append({"chat_key": chat_key, "message": message, **kwargs})
            or "notify-1"
        ),
    )

    assert chat_app.main() == 0

    assert seen["prompt"] == "List new emails"
    assert enqueued == [
        {
            "chat_key": "code@main",
            "message": "There are 2 new emails.",
            "source": "sub-agent:faltoochat",
            "session_id": "session-1",
        }
    ]
    assert capsys.readouterr().out == ""


def test_faltoochat_one_shot_notify_honors_source_override(
    tmp_path: Path, monkeypatch
) -> None:
    from types import SimpleNamespace

    workspace = tmp_path / "workspace"
    enqueued: list[dict[str, object]] = []

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "faltoochat",
            "Review PR",
            "--workspace",
            str(workspace),
            "--notify=code@main",
            "--source=sub-agent:pr-review",
        ],
    )
    monkeypatch.setattr(
        chat_app.sessions,
        "get_session",
        lambda **_kwargs: SimpleNamespace(session_id="session-1"),
    )

    async def fake_run_one_shot(session, prompt: str) -> str:
        return "Looks good."

    monkeypatch.setattr(chat_app, "_run_one_shot", fake_run_one_shot)
    monkeypatch.setattr(
        chat_app.notify_queue,
        "enqueue_notification",
        lambda chat_key, message, **kwargs: enqueued.append(kwargs) or "notify-1",
    )

    assert chat_app.main() == 0
    assert enqueued[0]["source"] == "sub-agent:pr-review"


def test_faltoochat_session_id_uses_existing_session(
    tmp_path: Path, monkeypatch
) -> None:
    from types import SimpleNamespace

    workspace = tmp_path / "workspace"
    messages_path = tmp_path / "messages.json"
    messages_path.touch()
    seen: dict[str, object] = {}

    class SessionRef:
        def __init__(self, chat_key: str, session_id: str) -> None:
            self.messages_path = messages_path

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "faltoochat",
            "Follow up",
            "--workspace",
            str(workspace),
            "--session-id=session-1",
        ],
    )
    monkeypatch.setattr(chat_app.sessions, "Session", SessionRef)
    monkeypatch.setattr(
        chat_app.sessions,
        "get_session",
        lambda *, chat_key, session_id=None, workspace=None: (
            seen.update({"session_id": session_id})
            or SimpleNamespace(chat_key=chat_key, session_id=session_id)
        ),
    )

    async def fake_run_one_shot(session, prompt: str) -> str:
        return "Follow-up answer."

    monkeypatch.setattr(chat_app, "_run_one_shot", fake_run_one_shot)

    assert chat_app.main() == 0
    assert seen["session_id"] == "session-1"


def test_faltoochat_session_id_fails_when_missing(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    messages_path = tmp_path / "missing.json"

    class SessionRef:
        def __init__(self, chat_key: str, session_id: str) -> None:
            self.messages_path = messages_path

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "faltoochat",
            "Follow up",
            "--workspace",
            str(workspace),
            "--session-id=missing-session",
        ],
    )
    monkeypatch.setattr(chat_app.sessions, "Session", SessionRef)

    try:
        chat_app.main()
    except SystemExit as exc:
        assert str(exc) == "Session not found: missing-session"
    else:
        raise AssertionError("expected SystemExit")


def test_faltoochat_notify_command_enqueues_notification(monkeypatch, capsys) -> None:
    enqueued: list[dict[str, object]] = []

    monkeypatch.setattr(
        sys,
        "argv",
        ["faltoochat", "notify", "code@main", "hello", "--source=script:test"],
    )
    monkeypatch.setattr(
        chat_app.notify_queue,
        "enqueue_notification",
        lambda chat_key, message, **kwargs: (
            enqueued.append({"chat_key": chat_key, "message": message, **kwargs})
            or "notify-1"
        ),
    )

    assert chat_app.main() == 0
    assert enqueued == [
        {"chat_key": "code@main", "message": "hello", "source": "script:test"}
    ]
    assert capsys.readouterr().out.strip() == "notify-1"


def test_faltoochat_notify_includes_fd_stderr(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import os
    from types import SimpleNamespace

    workspace = tmp_path / "workspace"
    enqueued: list[dict[str, object]] = []

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "faltoochat",
            "Warn me",
            "--workspace",
            str(workspace),
            "--notify=code@main",
        ],
    )
    monkeypatch.setattr(
        chat_app.sessions,
        "get_session",
        lambda **_kwargs: SimpleNamespace(session_id="session-1"),
    )

    async def fake_run_one_shot(session, prompt: str) -> str:
        os.write(2, b"native warning\n")
        return "answer"

    monkeypatch.setattr(chat_app, "_run_one_shot", fake_run_one_shot)
    monkeypatch.setattr(
        chat_app.notify_queue,
        "enqueue_notification",
        lambda chat_key, message, **kwargs: (
            enqueued.append({"chat_key": chat_key, "message": message, **kwargs})
            or "notify-1"
        ),
    )

    assert chat_app.main() == 0
    assert enqueued[0]["message"] == "answer\n\n## stderr\nnative warning"
    assert capsys.readouterr().out == ""


def test_faltoochat_notify_sends_error_notification_on_failure(
    tmp_path: Path, monkeypatch
) -> None:
    import os
    from types import SimpleNamespace

    workspace = tmp_path / "workspace"
    enqueued: list[dict[str, object]] = []

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "faltoochat",
            "Fail",
            "--workspace",
            str(workspace),
            "--notify=code@main",
        ],
    )
    monkeypatch.setattr(
        chat_app.sessions,
        "get_session",
        lambda **_kwargs: SimpleNamespace(session_id="session-1"),
    )

    async def fake_run_one_shot(session, prompt: str) -> str:
        os.write(2, b"native error\n")
        raise RuntimeError("boom")

    monkeypatch.setattr(chat_app, "_run_one_shot", fake_run_one_shot)
    monkeypatch.setattr(
        chat_app.notify_queue,
        "enqueue_notification",
        lambda chat_key, message, **kwargs: (
            enqueued.append({"chat_key": chat_key, "message": message, **kwargs})
            or "notify-1"
        ),
    )

    assert chat_app.main() == 1
    assert "native error" in str(enqueued[0]["message"])
    assert "RuntimeError: boom" in str(enqueued[0]["message"])
