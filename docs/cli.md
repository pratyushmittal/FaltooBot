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

### `faltoobot allow-group-chats`

List or update the WhatsApp group-chat allowlist without opening the full configure wizard.

```bash
faltoobot allow-group-chats list
faltoobot allow-group-chats add 15551234567
faltoobot allow-group-chats remove 15551234567
```

Add and remove accept one or more phone numbers / JIDs and normalize them before saving. If the WhatsApp service is already installed, Faltoobot restarts it so the new allowlist is applied.

### `faltoobot configure`

Shows a simple setup menu:
- Wizard
- WhatsApp
- Codex / OpenAI

Use the wizard unless you want to update only one part.

If a service is already installed, `configure` restarts it after saving changes.

### `faltoobot makemigrations`

Dev-only command used from the repo to create release migration scripts.

## Suggested flow

### First setup

```bash
uv tool install faltoobot
faltoobot configure
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

### `--notify-chat-key`

A one-shot run can send its final output back to another chat key. This powers sub-agents, cron jobs, and detached background jobs.

```bash
faltoochat "List new emails for the user" --workspace=./emails --notify-chat-key=code@main
```
