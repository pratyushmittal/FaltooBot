from pathlib import Path

from faltoobot.store import add_turn, create_cli_session


def test_add_turn_omits_duplicate_assistant_instructions(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = create_cli_session(sessions_dir, "CLI test", workspace)

    session = add_turn(session, "assistant", "first", instructions="same")
    session = add_turn(session, "assistant", "second", instructions="same")
    session = add_turn(session, "assistant", "third", instructions="changed")

    assert session.messages[0].instructions == "same"
    assert session.messages[1].instructions is None
    assert session.messages[2].instructions == "changed"
