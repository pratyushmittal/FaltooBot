import json
from pathlib import Path
from typing import Any, cast

from faltoobot.config import (
    Config,
    build_config,
    load_toml,
    merge_config,
    render_config,
)
from faltoobot.sessions import (
    MESSAGES_FILE,
    generated_image_developer_message,
    save_generated_image,
)


def _version_tuple(version: str | None) -> tuple[int, ...]:
    if not version:
        return ()
    return tuple(int(part) for part in version.split(".") if part.isdigit())


def _upgrading_across(
    previous_version: str | None,
    current_version: str | None,
    target_version: str,
) -> bool:
    previous = _version_tuple(previous_version)
    current = _version_tuple(current_version)
    target = _version_tuple(target_version)
    return bool(previous and current and previous < target <= current)


def update_default_openai_model(config: Config) -> bool:
    path = config.config_file
    if not path.exists():
        # comment: fresh installs will be created with the current default model.
        return False
    data = merge_config(load_toml(path))
    if data["openai"]["model"] != "gpt-5.4":
        # comment: only move users who were still on the previous default.
        return False
    data["openai"]["model"] = "gpt-5.5"
    path.write_text(render_config(data), encoding="utf-8")
    return True


def disable_default_openai_websocket(
    config: Config,
    *,
    previous_version: str | None,
    current_version: str | None,
) -> bool:
    if not _upgrading_across(previous_version, current_version, "7.0.0"):
        # comment: keep the default flip tied to this release only.
        return False
    path = config.config_file
    if not path.exists():
        # comment: fresh installs will be created with websocket disabled.
        return False
    data = merge_config(load_toml(path))
    if data["openai"]["websocket"] is not True:
        # comment: false configs are already using the safer SDK streaming path.
        return False
    data["openai"]["websocket"] = False
    path.write_text(render_config(data), encoding="utf-8")
    return True


def _migrated_generated_image_messages(
    messages: list[dict[str, Any]], workspace: Path
) -> tuple[list[dict[str, Any]], bool]:
    migrated: list[dict[str, Any]] = []
    for item in messages:
        migrated.append(item)
        result = item.get("result")
        if item.get("type") == "image_generation_call" and isinstance(result, str):
            path = save_generated_image(result, workspace)
            migrated.append(generated_image_developer_message(path))
    return migrated, len(migrated) != len(messages)


def add_generated_image_developer_messages(config: Config) -> bool:
    developer_prefix = "Generated images are saved locally at: "
    changed_any = False
    for messages_path in config.sessions_dir.glob(f"*/*/{MESSAGES_FILE}"):
        text = messages_path.read_text(encoding="utf-8")
        if "image_generation_call" not in text or developer_prefix in text:
            continue

        payload = cast(dict[str, Any], json.loads(text))
        migrated, changed = _migrated_generated_image_messages(
            cast(list[dict[str, Any]], payload["messages"]),
            Path(cast(str, payload["workspace"])),
        )
        if not changed:
            continue

        payload["messages"] = migrated
        messages_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        changed_any = True

    return changed_any


def main(
    config: Config | None = None,
    *,
    previous_version: str | None = None,
    current_version: str | None = None,
) -> list[str]:
    config = config or build_config()
    changes: list[str] = []
    # comment: keep update summaries quiet when idempotent migrations have no work.
    if update_default_openai_model(config):
        changes.append("migration:update-default-openai-model")
    if disable_default_openai_websocket(
        config, previous_version=previous_version, current_version=current_version
    ):
        changes.append("migration:disable-default-openai-websocket")
    if add_generated_image_developer_messages(config):
        changes.append("migration:add-generated-image-developer-messages")
    return changes
