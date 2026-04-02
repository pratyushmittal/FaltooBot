import json
from pathlib import Path

from faltoobot.cli import app as cli
from faltoobot.cli import migrations
from faltoobot.config import Config


def make_config(tmp_path: Path) -> Config:
    home = tmp_path / "home"
    root = home / ".faltoobot"
    return Config(
        home=home,
        root=root,
        config_file=root / "config.toml",
        log_file=root / "faltoobot.log",
        sessions_dir=root / "sessions",
        session_db=root / "session.db",
        launch_agent=home / "Library" / "LaunchAgents" / "com.faltoobot.agent.plist",
        run_script=root / "run.sh",
        openai_api_key="",
        openai_oauth="",
        openai_model="gpt-5.4",
        openai_thinking="high",
        openai_fast=False,
        openai_transcription_model="gpt-4o-transcribe",
        allow_groups=False,
        allowed_chats=set(),
    )


def write_migration(root: Path, version: str, body: str) -> None:
    path = root / "migrations" / version
    path.mkdir(parents=True, exist_ok=True)
    (path / "migrate.py").write_text(body, encoding="utf-8")


def test_run_release_migrations_runs_once_in_order(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    write_migration(
        tmp_path,
        "0.4.0",
        "def migrate(config):\n    path = config.root / 'ran.txt'\n    path.parent.mkdir(parents=True, exist_ok=True)\n    path.write_text('0.4.0\\n', encoding='utf-8')\n",
    )
    write_migration(
        tmp_path,
        "0.5.0",
        "def migrate(config):\n    path = config.root / 'ran.txt'\n    old = path.read_text(encoding='utf-8') if path.exists() else ''\n    path.write_text(old + '0.5.0\\n', encoding='utf-8')\n",
    )

    first = migrations.run_release_migrations(config, tmp_path)
    second = migrations.run_release_migrations(config, tmp_path)

    assert first == ["0.4.0", "0.5.0"]
    assert second == []
    assert (config.root / "ran.txt").read_text(encoding="utf-8") == "0.4.0\n0.5.0\n"
    assert json.loads(
        (config.root / migrations.STATE_FILE).read_text(encoding="utf-8")
    ) == {"applied_versions": ["0.4.0", "0.5.0"]}


def test_run_release_migrations_ignores_invalid_state_file(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    state = config.root / migrations.STATE_FILE
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{bad json", encoding="utf-8")
    write_migration(
        tmp_path,
        "0.5.0",
        "def migrate(config):\n    (config.root / 'ok.txt').parent.mkdir(parents=True, exist_ok=True)\n    (config.root / 'ok.txt').write_text('ok', encoding='utf-8')\n",
    )

    ran = migrations.run_release_migrations(config, tmp_path)

    assert ran == ["0.5.0"]
    assert (config.root / "ok.txt").read_text(encoding="utf-8") == "ok"


def test_cli_run_migrations_includes_release_migrations(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    write_migration(
        tmp_path,
        "0.5.0",
        "def migrate(config):\n    (config.root / 'migrated.txt').parent.mkdir(parents=True, exist_ok=True)\n    (config.root / 'migrated.txt').write_text('done', encoding='utf-8')\n",
    )
    monkeypatch.setattr(cli, "project_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "has_service", lambda config: False)

    changes = __import__("asyncio").run(cli.run_migrations(config))

    assert "sessions" in changes
    assert "migration:0.5.0" in changes
    assert (config.root / "migrated.txt").read_text(encoding="utf-8") == "done"
