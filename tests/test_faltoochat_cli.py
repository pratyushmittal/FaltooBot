import sys
from pathlib import Path

from faltoobot.faltoochat import app as chat_app


def test_faltoochat_main_runs_one_shot_and_enqueues_notification(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
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
            "--notify-chat-key",
            "code@main",
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
    monkeypatch.setattr(
        chat_app.notify_queue, "format_subagent_message", lambda **_: "formatted"
    )
    monkeypatch.setattr(
        chat_app.notify_queue,
        "enqueue_notification",
        lambda chat_key, message: (
            seen.update({"notify_chat_key": chat_key, "notify_message": message})
            or "notify-1"
        ),
    )

    result = chat_app.main()

    assert result == 0
    assert workspace.exists()
    assert seen["prompt"] == "List new emails"
    assert str(seen["chat_key"]).startswith("sub-agent@")
    assert seen["notify_chat_key"] == "code@main"
    assert seen["notify_message"] == "formatted"
    assert capsys.readouterr().out.strip() == "There are 2 new emails."


def test_faltoochat_main_rejects_notify_without_prompt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(
        sys,
        "argv",
        ["faltoochat", "--workspace", str(workspace), "--notify-chat-key", "code@main"],
    )
    monkeypatch.setattr(
        chat_app.sessions,
        "get_session",
        lambda **_: ("code@workspace", "session-1"),
    )

    try:
        chat_app.main()
    except SystemExit as exc:
        assert str(exc) == "--notify-chat-key requires a prompt"
    else:
        raise AssertionError("Expected SystemExit")
