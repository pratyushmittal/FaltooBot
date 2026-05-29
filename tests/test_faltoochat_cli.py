import logging
import sys
from pathlib import Path
from types import SimpleNamespace

from faltoobot.faltoochat import app as chat_app
from faltoobot.faltoochat.logging_config import configure_logging


def _remove_faltoochat_log_handlers() -> None:
    logger = logging.getLogger("faltoobot")
    for handler in list(logger.handlers):
        if getattr(handler, "_faltoochat_handler", False):
            logger.removeHandler(handler)
            handler.close()


def _session_stub(
    root: Path, *, chat_key: str = "code@test", session_id: str = "session-1"
) -> SimpleNamespace:
    return SimpleNamespace(
        chat_key=chat_key,
        session_id=session_id,
        chat_root=root / "sessions" / chat_key,
    )


def test_configure_logging_writes_faltoochat_log_with_session_id(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "logs" / "faltoochat.log"

    try:
        configure_logging(log_path, session_id="session-1")
        logger = logging.getLogger("faltoobot")
        logger.info("hello")
        for handler in logger.handlers:
            handler.flush()

        text = log_path.read_text(encoding="utf-8")
        assert "INFO faltoobot [session_id=session-1]: hello" in text
    finally:
        _remove_faltoochat_log_handlers()


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
            or _session_stub(tmp_path, chat_key=chat_key)
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
            or _session_stub(tmp_path, chat_key=chat_key)
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
        lambda **_kwargs: _session_stub(tmp_path),
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
            or _session_stub(
                tmp_path, chat_key=chat_key, session_id=session_id or "session-1"
            )
        ),
    )

    async def fake_run_one_shot(session, prompt: str) -> str:
        return "Follow-up answer."

    monkeypatch.setattr(chat_app, "_run_one_shot", fake_run_one_shot)

    assert chat_app.main() == 0
    assert seen["session_id"] == "session-1"


def test_faltoochat_session_id_without_workspace_finds_saved_session(
    tmp_path: Path, monkeypatch
) -> None:
    app_root = tmp_path / "home"
    messages_path = (
        app_root / "sessions/sub-agent@project-abc123/session-1/messages.json"
    )
    messages_path.parent.mkdir(parents=True)
    messages_path.write_text("{}", encoding="utf-8")
    seen: dict[str, object] = {}

    monkeypatch.setattr(chat_app.sessions, "app_root", lambda: app_root)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "faltoochat",
            "Follow up",
            "--session-id=session-1",
        ],
    )
    monkeypatch.setattr(
        chat_app.sessions,
        "get_session",
        lambda *, chat_key, session_id=None, workspace=None: (
            seen.update(
                {"chat_key": chat_key, "session_id": session_id, "workspace": workspace}
            )
            or _session_stub(
                app_root, chat_key=chat_key, session_id=session_id or "session-1"
            )
        ),
    )

    async def fake_run_one_shot(session, prompt: str) -> str:
        return "Follow-up answer."

    monkeypatch.setattr(chat_app, "_run_one_shot", fake_run_one_shot)

    assert chat_app.main() == 0
    assert seen == {
        "chat_key": "sub-agent@project-abc123",
        "session_id": "session-1",
        "workspace": None,
    }


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


def test_faltoochat_notify_requires_prompt(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["faltoochat", "--notify=code@main"])

    try:
        chat_app.main()
    except SystemExit as exc:
        assert str(exc) == "--notify requires a prompt"
    else:
        raise AssertionError("expected SystemExit")


def test_faltoochat_source_requires_notify(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["faltoochat", "Prompt", "--source=cron:test"])

    try:
        chat_app.main()
    except SystemExit as exc:
        assert str(exc) == "--source requires --notify"
    else:
        raise AssertionError("expected SystemExit")


def test_faltoochat_notify_includes_fd_stderr(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import os

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
        lambda **_kwargs: _session_stub(tmp_path),
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
        lambda **_kwargs: _session_stub(tmp_path),
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
