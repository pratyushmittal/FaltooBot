from faltoobot.config import Config, build_config


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


def main(config: Config | None = None) -> list[str]:
    config = config or build_config()
    changes: list[str] = []
    # comment: keep update summaries quiet when this idempotent migration has no work.
    if remove_session_last_used_files(config):
        changes.append("migration:remove-session-last-used")
    return changes
