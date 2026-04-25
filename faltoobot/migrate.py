from faltoobot.config import (
    Config,
    build_config,
    load_toml,
    merge_config,
    render_config,
)


def remove_session_last_used_files(config: Config) -> bool:
    sessions_dir = config.sessions_dir
    if not sessions_dir.exists():
        # comment: fresh installs do not have session state yet.
        return False
    removed = False
    for path in sessions_dir.glob("*/last_used"):
        # comment: only remove the old marker file, not unrelated folders.
        if path.is_file():
            path.unlink()
            removed = True
    return removed


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


def main(config: Config | None = None) -> list[str]:
    config = config or build_config()
    changes: list[str] = []
    # comment: keep update summaries quiet when idempotent migrations have no work.
    if remove_session_last_used_files(config):
        changes.append("migration:remove-session-last-used")
    if update_default_openai_model(config):
        changes.append("migration:update-default-openai-model")
    return changes
