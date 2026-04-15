from pathlib import Path

import pytest

from faltoobot import notify_queue


def _use_temp_app_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(notify_queue, "app_root", lambda: tmp_path / ".faltoobot")


def _notification_payload(
    *, message: str, source: str | None = None
) -> notify_queue.Notification:
    payload: notify_queue.Notification = {
        "id": "notify-1",
        "chat_key": "code@demo",
        "message": message,
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    if source is not None:
        payload["source"] = source
    return payload


def test_notify_queue_enqueues_claims_and_acks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_temp_app_root(monkeypatch, tmp_path)

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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_temp_app_root(monkeypatch, tmp_path)
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


@pytest.mark.parametrize(
    ("payload", "expected_parts", "unexpected_parts"),
    [
        pytest.param(
            _notification_payload(
                message="Check backups.",
                source="cron:daily-ops",
            ),
            [
                "# Notification",
                "Reply with [noreply]",
                "source: cron:daily-ops",
                "## message",
                "Check backups.",
            ],
            [],
            id="with-source",
        ),
        pytest.param(
            _notification_payload(message="hello"),
            [
                "# Notification",
                "Reply with [noreply]",
                "## message",
                "hello",
            ],
            ["source:"],
            id="without-source",
        ),
    ],
)
def test_format_notification_message_layout(
    payload: notify_queue.Notification,
    expected_parts: list[str],
    unexpected_parts: list[str],
) -> None:
    message = notify_queue.format_notification_message(payload)

    for part in expected_parts:
        assert part in message
    for part in unexpected_parts:
        assert part not in message
