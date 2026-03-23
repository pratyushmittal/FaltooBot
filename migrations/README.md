# Migrations

Put release-specific migration scripts in version folders here.

Use this layout:

```text
migrations/
  0.4.0/
    migrate.py
```

Only add a version folder when that release needs a migration.

Each migration script should define:

```python
def migrate(config):
    ...
```

Migrations run once in version order and are tracked in `~/.faltoobot/migration-state.json`.

## Good practices

- Keep migrations idempotent. A migration should be safe to run again without corrupting state.
- Keep migrations safe on fresh installs. If the old file or directory does not exist yet, return early.
- Prefer small, non-destructive changes. Rename or copy before deleting when possible.
- Scope each migration to one release and one clear job.
- Create parent directories before writing files.
- Add comments on guard clauses when handling missing or already-migrated state.

A good migration usually looks like this:

```python
def migrate(config):
    path = config.root / "legacy-file.json"
    if not path.exists():
        # comment: fresh installs or already-migrated users will not have this file.
        return

    ...
```
