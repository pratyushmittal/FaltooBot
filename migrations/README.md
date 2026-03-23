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
