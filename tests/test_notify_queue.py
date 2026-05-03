from pathlib import Path

from faltoobot import notify_queue


def test_notify_queue_enqueues_claims_and_acks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(notify_queue, "app_root", lambda: tmp_path / ".faltoobot")

    notification_id = notify_queue.enqueue_notification(
        "code@demo", "hello", source="cron:daily-ops"
    )
    claimed = notify_queue.claim_notifications(
        lambda item: item["chat_key"] == "code@demo"
    )

    assert notification_id.startswith("notify_")
    assert len(claimed) == 1
    path, notification = claimed[0]
    assert notification["message"] == "hello"
    assert notification["source"] == "cron:daily-ops"
    assert path.exists()

    notify_queue.ack_notification(path)

    assert not path.exists()


def test_notify_queue_requeues_claimed_notifications(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(notify_queue, "app_root", lambda: tmp_path / ".faltoobot")
    notify_queue.enqueue_notification("code@demo", "hello")

    claimed = notify_queue.claim_notifications(
        lambda item: item["chat_key"] == "code@demo"
    )
    path, notification = claimed[0]

    notify_queue.requeue_notification(path)

    claimed_again = notify_queue.claim_notifications(
        lambda item: item["chat_key"] == notification["chat_key"]
    )
    assert len(claimed_again) == 1
    notify_queue.ack_notification(claimed_again[0][0])


def test_format_notification_message_uses_expected_layout() -> None:
    message = notify_queue.format_notification_message(
        {
            "id": "notify-1",
            "chat_key": "code@demo",
            "message": "Check backups.",
            "created_at": "2026-01-01T00:00:00+00:00",
            "source": "cron:daily-ops",
        }
    )

    assert "# Background update" in message
    assert "[noreply]" not in message
    assert "source: cron:daily-ops" in message
    assert "## message" in message
    assert "Check backups." in message


def test_format_notification_message_without_source_still_wraps_message() -> None:
    message = notify_queue.format_notification_message(
        {
            "id": "notify-1",
            "chat_key": "code@demo",
            "message": "hello",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )

    assert "# Background update" in message
    assert "[noreply]" not in message
    assert "## message" in message
    assert "hello" in message
