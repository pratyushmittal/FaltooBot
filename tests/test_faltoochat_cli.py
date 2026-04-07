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
