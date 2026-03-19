from pathlib import Path

from faltoobot.store import add_turn, create_session, session_items, sync_assistant_turn

MESSAGE_COUNT = 2


def test_add_turn_omits_duplicate_assistant_instructions(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = create_session(sessions_dir, "CLI test", kind="cli", workspace=workspace)

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
    session = create_session(sessions_dir, "CLI test", kind="cli", workspace=workspace)

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


def test_sync_assistant_turn_replaces_in_progress_assistant_turn(
    tmp_path: Path,
) -> None:
    sessions_dir = tmp_path / "sessions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = create_session(sessions_dir, "CLI test", kind="cli", workspace=workspace)
    session = add_turn(session, "user", "hi")

    session = sync_assistant_turn(
        session,
        "",
        items=[
            {"type": "shell_call", "call_id": "call_1", "action": {"commands": ["pwd"]}}
        ],
    )
    created_at = session.messages[-1].created_at
    session = sync_assistant_turn(
        session,
        "done",
        items=[
            {
                "type": "shell_call",
                "call_id": "call_1",
                "action": {"commands": ["pwd"]},
            },
            {
                "type": "shell_call_output",
                "call_id": "call_1",
                "status": "completed",
                "output": [
                    {
                        "stdout": "/tmp",
                        "stderr": "",
                        "outcome": {"type": "exit", "exit_code": 0},
                    }
                ],
            },
        ],
    )

    assert len(session.messages) == MESSAGE_COUNT
    assert session.messages[-1].role == "assistant"
    assert session.messages[-1].content == "done"
    assert session.messages[-1].created_at == created_at
    assert [item["type"] for item in session.messages[-1].items] == [
        "shell_call",
        "shell_call_output",
    ]


def test_session_items_append_assistant_text_when_items_have_only_tools(
    tmp_path: Path,
) -> None:
    sessions_dir = tmp_path / "sessions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = create_session(sessions_dir, "CLI test", kind="cli", workspace=workspace)

    session = add_turn(
        session,
        "assistant",
        "search-backed answer",
        items=[{"type": "web_search_call", "id": "ws_1", "status": "completed"}],
    )

    assert session_items(session) == [
        {"type": "web_search_call", "id": "ws_1", "status": "completed"},
        {"type": "message", "role": "assistant", "content": "search-backed answer"},
    ]


def test_session_items_do_not_duplicate_existing_assistant_message_item(
    tmp_path: Path,
) -> None:
    sessions_dir = tmp_path / "sessions"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = create_session(sessions_dir, "CLI test", kind="cli", workspace=workspace)

    session = add_turn(
        session,
        "assistant",
        "search-backed answer",
        items=[
            {"type": "web_search_call", "id": "ws_1", "status": "completed"},
            {"type": "message", "role": "assistant", "content": "search-backed answer"},
        ],
    )

    assert session_items(session) == [
        {"type": "web_search_call", "id": "ws_1", "status": "completed"},
        {"type": "message", "role": "assistant", "content": "search-backed answer"},
    ]
