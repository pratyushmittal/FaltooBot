# CLI

Faltoobot now has a small public CLI surface.

## Main commands

### `faltoobot update`

Upgrades the uv-installed tool, ensures config exists, and runs migrations.

### `faltoobot whatsapp`

The main happy-path command. It:
- runs update
- stops any old service
- installs the service
- starts the service
- follows logs

Use this when you want to make the bot work.

### `faltoobot logs`

Shows logs in follow mode.

### `faltoobot codex-login`

Signs in with Codex / ChatGPT OAuth and saves the auth file path in `~/.faltoobot/config.toml`.

For other settings, edit `~/.faltoobot/config.toml` directly. `faltoobot update` creates this file with defaults when it does not exist. Set `bot.allowed_chats` before starting WhatsApp if you want to restrict who can talk to the bot.

## Suggested flow

### First setup

```bash
uv tool install faltoobot
faltoobot update
faltoobot codex-login
# edit ~/.faltoobot/config.toml if needed
faltoobot whatsapp
```

### Later updates

```bash
faltoobot update
```

or just run:

```bash
faltoobot whatsapp
```

## Notes

- `faltoobot whatsapp` is safe to run again.
- `Ctrl+C` stops log following, not the background service.
- `faltoobot logs` is the command to reattach to logs later.

## `faltoochat`

`faltoochat` now supports both interactive and one-shot usage.

### Interactive mode

Run it without a prompt to open the terminal UI:

```bash
faltoochat
faltoochat --workspace=./repo
faltoochat --new-session
```

### One-shot mode

Run it with a prompt to execute a single task and print the final output to stdout:

```bash
faltoochat "draft a release note"
faltoochat "review unstaged files" --workspace=./repo --new-session
```

### `--notify`

A one-shot run can send its final output back to another chat key. `--notify-chat-key` remains accepted as a compatibility alias. This powers sub-agents, cron jobs, and detached background jobs.

```bash
faltoochat "List new emails for the user" --workspace=./emails --notify=code@main
```
