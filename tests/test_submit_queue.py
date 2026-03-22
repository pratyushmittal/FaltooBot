from pathlib import Path

from faltoobot import sessions, submit_queue


def build_session(tmp_path: Path, monkeypatch) -> sessions.Session:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return sessions.get_session(
        chat_key=sessions.get_dir_chat_key(workspace),
        workspace=workspace,
    )


def test_submit_queue_stores_messages_under_chat_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    session = build_session(tmp_path, monkeypatch)

    queue = submit_queue.add_to_queue(
        session,
        {"type": "message", "role": "user", "content": "hello"},
    )

    assert len(queue) == 1
    assert queue[0]["content"] == "hello"
    assert queue[0]["id"]
    assert submit_queue.get_queue(session) == queue
    assert (
        sessions.get_messages_path(session).parent.parent
        / submit_queue.SUBMIT_QUEUE_FILE
    ).exists()


def test_submit_queue_updates_order_and_auto_submit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    session = build_session(tmp_path, monkeypatch)
    queue = submit_queue.add_to_queue(
        session,
        {"type": "message", "role": "user", "content": "first"},
    )
    first_id = queue[0]["id"]
    queue = submit_queue.add_to_queue(
        session,
        {"type": "message", "role": "user", "content": "second"},
    )
    second_id = queue[1]["id"]

    queue = submit_queue.move_up(session, second_id)
    assert [item["content"] for item in queue] == ["second", "first"]

    queue = submit_queue.move_down(session, second_id)
    assert [item["content"] for item in queue] == ["first", "second"]

    queue = submit_queue.set_auto_submit(session, first_id)
    assert queue[0]["auto_submit"] is True

    queue = submit_queue.remove_auto_submit(session, first_id)
    assert "auto_submit" not in queue[0]

    queue = submit_queue.remove_from_queue(session, first_id)
    assert [item["id"] for item in queue] == [second_id]
