from pathlib import Path

from faltoobot import changelog


def test_changelog_between_returns_matching_versions(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "# Changelog\n\n"
        "## 6.1.0 — 2026-05-09\n\n"
        "### Changed\n- New thing\n\n"
        "## 6.0.0 — 2026-05-08\n\n"
        "### Fixed\n- Old thing\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(changelog, "_changelog_path", lambda: path)

    assert changelog.changelog_between("6.0.0", "6.1.0") == (
        "## 6.1.0 — 2026-05-09\n\n### Changed\n- New thing"
    )


def test_record_and_consume_changelog_update(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / ".faltoobot"
    notifications: list[dict[str, object]] = []
    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "# Changelog\n\n## 6.1.0 — 2026-05-09\n\n- Updated\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(changelog, "app_root", lambda: root)
    monkeypatch.setattr(changelog, "_changelog_path", lambda: path)
    monkeypatch.setattr(
        changelog.notify_queue,
        "enqueue_notification",
        lambda chat_key, message, **kwargs: (
            notifications.append({"chat_key": chat_key, "message": message, **kwargs})
            or "notify-1"
        ),
    )

    changelog.record_update("6.0.0", "6.1.0")

    assert notifications == [
        {
            "chat_key": "global",
            "message": "## 6.1.0 — 2026-05-09\n\n- Updated",
            "source": "faltoobot update",
        }
    ]
    assert "## 6.1.0" in changelog.consume_changelog_update()
    assert changelog.consume_changelog_update() == ""


def test_available_update_notice(monkeypatch) -> None:
    monkeypatch.setattr(changelog, "_latest_package_version", lambda: "6.2.0")

    assert changelog.available_update_notice("6.1.0") == (
        "New Faltoobot version available: 6.2.0 "
        "(current 6.1.0). Run `faltoobot update` to upgrade."
    )


def test_available_update_notice_returns_empty_for_current_version(monkeypatch) -> None:
    monkeypatch.setattr(changelog, "_latest_package_version", lambda: "6.2.0")

    assert changelog.available_update_notice("6.2.0") == ""
