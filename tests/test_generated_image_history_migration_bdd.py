import base64
import json
from pathlib import Path
from typing import Any, cast

import pytest
from pytest_bdd import given, scenarios, then, when

from faltoobot.config import Config
from faltoobot import migrate

scenarios("features/generated_image_history_migration.feature")


@pytest.fixture
def migration_ctx(tmp_path: Path) -> dict[str, Any]:
    home = tmp_path / "home"
    root = home / ".faltoobot"
    config = Config(
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
        openai_model="gpt-5.6-sol",
        openai_thinking="high",
        openai_fast=False,
        openai_transcription_model="gpt-4o-transcribe",
        allow_group_chats=set(),
        allowed_chats=set(),
        bot_name="Faltoo",
        browser_binary="",
    )
    return {
        "config": config,
        "workspace": tmp_path / "workspace",
        "messages_path": config.sessions_dir / "chat" / "session" / "messages.json",
    }


@given("a saved chat history with an old generated image call")
def saved_history_with_old_generated_image_call(
    migration_ctx: dict[str, Any],
) -> None:
    messages_path = cast(Path, migration_ctx["messages_path"])
    payload = {
        "id": "session",
        "chat_key": "chat",
        "workspace": str(migration_ctx["workspace"]),
        "system_prompt": "",
        "message_ids": [],
        "messages": [
            {"type": "message", "role": "user", "content": "draw"},
            {
                "type": "image_generation_call",
                "id": "ig_1",
                "status": "completed",
                "result": base64.b64encode(b"png-bytes").decode("utf-8"),
            },
        ],
    }
    messages_path.parent.mkdir(parents=True)
    messages_path.write_text(json.dumps(payload), encoding="utf-8")


@given("a saved chat history that already has a generated image developer note")
def saved_history_with_generated_image_developer_note(
    migration_ctx: dict[str, Any],
) -> None:
    messages_path = cast(Path, migration_ctx["messages_path"])
    payload = {
        "workspace": str(migration_ctx["workspace"]),
        "messages": [
            {
                "type": "image_generation_call",
                "id": "ig_1",
                "status": "completed",
                "result": base64.b64encode(b"png-bytes").decode("utf-8"),
            },
            {
                "type": "message",
                "role": "developer",
                "content": [
                    {
                        "type": "input_text",
                        "text": "![Generated image](.generated-images/cat.png)",
                    }
                ],
            },
        ],
    }
    messages_path.parent.mkdir(parents=True)
    messages_path.write_text(json.dumps(payload), encoding="utf-8")
    migration_ctx["original_payload"] = payload


@when("update migrations run")
def update_migrations_run(migration_ctx: dict[str, Any]) -> None:
    config = cast(Config, migration_ctx["config"])
    migration_ctx["changes"] = migrate.main(
        config, previous_version="7.4.1", current_version="7.5.0"
    )
    messages_path = cast(Path, migration_ctx["messages_path"])
    migration_ctx["payload"] = json.loads(messages_path.read_text(encoding="utf-8"))


@then("the update summary includes the generated image developer migration")
def update_summary_includes_generated_image_migration(
    migration_ctx: dict[str, Any],
) -> None:
    assert migration_ctx["changes"] == [
        "migration:add-generated-image-developer-messages"
    ]


@then("the generated image is saved in the workspace")
def generated_image_is_saved_in_workspace(migration_ctx: dict[str, Any]) -> None:
    developer = migration_ctx["payload"]["messages"][2]
    text = developer["content"][0]["text"]
    image_path = cast(Path, migration_ctx["workspace"]) / text.removeprefix(
        "![Generated image]("
    ).removesuffix(")")
    assert image_path.read_bytes() == b"png-bytes"


@then("the history includes a developer note with the local image path")
def history_includes_developer_note_with_local_image_path(
    migration_ctx: dict[str, Any],
) -> None:
    developer = migration_ctx["payload"]["messages"][2]
    assert developer["role"] == "developer"
    text = developer["content"][0]["text"]
    assert text.startswith("![Generated image](.generated-images/")


@then("the update summary is empty")
def update_summary_is_empty(migration_ctx: dict[str, Any]) -> None:
    assert migration_ctx["changes"] == []


@then("the saved chat history is unchanged")
def saved_chat_history_is_unchanged(migration_ctx: dict[str, Any]) -> None:
    assert migration_ctx["payload"] == migration_ctx["original_payload"]


@when("update migrations run after the generated image release")
def update_migrations_run_after_release(migration_ctx: dict[str, Any]) -> None:
    config = cast(Config, migration_ctx["config"])
    migration_ctx["changes"] = migrate.main(
        config, previous_version="7.5.0", current_version="7.5.1"
    )
    messages_path = cast(Path, migration_ctx["messages_path"])
    migration_ctx["payload"] = json.loads(messages_path.read_text(encoding="utf-8"))


@then("the old generated image history is unchanged")
def old_generated_image_history_is_unchanged(
    migration_ctx: dict[str, Any],
) -> None:
    assert [item["type"] for item in migration_ctx["payload"]["messages"]] == [
        "message",
        "image_generation_call",
    ]
    assert not (cast(Path, migration_ctx["workspace"]) / ".generated-images").exists()
