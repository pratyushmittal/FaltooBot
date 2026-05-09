from pathlib import Path

from faltoobot import changelog


def test_private_changelog_between_returns_matching_versions(
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

    assert changelog._changelog_between("6.0.0", "6.1.0") == (
        "## 6.1.0 — 2026-05-09\n\n### Changed\n- New thing"
    )


def test_record_and_consume_changelog_update(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / ".faltoobot"
    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "# Changelog\n\n## 6.1.0 — 2026-05-09\n\n- Updated\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(changelog, "app_root", lambda: root)
    monkeypatch.setattr(changelog, "_changelog_path", lambda: path)

    changelog.record_update("6.0.0", "6.1.0")

    assert "## 6.1.0" in changelog.consume_changelog_update()
    assert changelog.consume_changelog_update() == ""
