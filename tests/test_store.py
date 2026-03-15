from pathlib import Path

from faltoobot.store import add_turn, create_cli_session, session_items


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


def test_session_items_preserve_user_message_items(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = create_cli_session(sessions_dir, "CLI test", workspace)

    session = add_turn(
        session,
        "user",
        "Look\n[image: cat.png]",
        items=[
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Look"},
                    {"type": "input_image", "file_id": "file_123", "detail": "auto"},
                ],
            }
        ],
    )

    assert session_items(session) == [
        {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Look"},
                {"type": "input_image", "file_id": "file_123", "detail": "auto"},
            ],
        }
    ]
