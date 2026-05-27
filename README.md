# faltoobot

[![tests](https://img.shields.io/github/actions/workflow/status/pratyushmittal/FaltooBot/main.yml?branch=main&label=tests)](https://github.com/pratyushmittal/FaltooBot/actions/workflows/main.yml)
![coverage](https://img.shields.io/badge/coverage-78%25-brightgreen)

`faltoobot` is a personal assistant that lives on its own WhatsApp account.

## How it works

- Get a separate SIM / WhatsApp account for Faltoobot.
- Sign in to that account on a spare phone.
- Install `faltoobot` on a computer that will stay online.
- Run `faltoobot update` once after install to create `~/.faltoobot/config.toml`.
- Edit `~/.faltoobot/config.toml` if you need to change defaults.
- Run `faltoobot codex-login` to sign in with Codex / ChatGPT OAuth, or set `openai.api_key` in config.
- Run `faltoobot whatsapp` to keep the bot running.
- Message that WhatsApp number from your own number.

## Install

```bash
uv tool install faltoobot
```

Then run the setup/update step once:

```bash
faltoobot update
```

This creates `~/.faltoobot/config.toml` with defaults. You can run `faltoobot update` again later to upgrade Faltoobot and run migrations.

Then you can run `faltoobot` and `faltoochat` from any folder.

If uv asks you to add its tool bin directory to your `PATH`, run:

```bash
uv tool update-shell
```

For faster project and code search inside `faltoochat`, install `ripgrep` (`rg`) too:

```bash
# macOS
brew install ripgrep

# Ubuntu / Debian
sudo apt-get update && sudo apt-get install -y ripgrep

# Fedora
sudo dnf install ripgrep

# Arch Linux
sudo pacman -S ripgrep
```

`faltoochat` falls back without `rg`, but search is faster and more reliable when it is installed.

## Quick start

### 1. Update and configure

```bash
faltoobot update
```

This creates `~/.faltoobot/config.toml` with defaults. Edit that file directly whenever you need to change settings.

For Codex / ChatGPT OAuth, run:

```bash
faltoobot codex-login
```

Or set `openai.api_key` in `~/.faltoobot/config.toml`.

Example config:

```toml
[openai]
api_key = "your_key_here"
oauth = ""
model = "gpt-5.5"
thinking = "high"
fast = false
websocket = true

[bot]
allow_group_chats = ["120363000000000000@g.us"]
allowed_chats = ["15551234567"]
bot_name = "Faltoo"
```

If `oauth` is set, Faltoobot prefers that OAuth auth file over `api_key`. If `oauth` is blank, Faltoobot falls back to `OPENAI_API_KEY` from the environment.

By default, `websocket = true` uses the Responses API WebSocket mode for lower-latency tool loops with either an API key or ChatGPT/Codex OAuth. Set it to `false` to use normal HTTP streaming.

Set `allowed_chats` to the WhatsApp phone numbers that should be allowed to it in direct chats. Use WhatsApp phone numbers or JIDs. Faltoobot normalizes phone numbers into WhatsApp JIDs when saving the config.

Set `allow_group_chats` to the group JIDs that the bot should keep history for and reply in. If a non-approved group mentions the bot, Faltoobot DMs `allowed_chats` with `/approve_group <group_jid>` and `/deny_group <group_jid>` instructions. In groups with more than two people, the bot replies only when mentioned or when someone replies to the bot.

### 2. Start WhatsApp service

```bash
faltoobot whatsapp
```

This is the main command for running Faltoobot. It:
- upgrades the installed tool with uv
- ensures config exists
- runs migrations
- stops any old Faltoobot service
- installs the service
- starts the service
- follows logs in the current terminal

Press `Ctrl+C` any time. The service keeps running in the background.

### 3. Watch logs later

```bash
faltoobot logs
```

## Commands

### `faltoobot update`

```bash
faltoobot update
```

Upgrades the installed tool with uv, ensures config exists, and runs migrations.

If uv installs a newer version, Faltoobot asks you to rerun the command so the rest of the flow continues with the newer installed version.

### `faltoobot whatsapp`

```bash
faltoobot whatsapp
```

Best command for normal use. It runs update, refreshes the background service, and follows logs.

### `faltoobot logs`

```bash
faltoobot logs
```

Shows log output in follow mode.

### `faltoobot codex-login`

```bash
faltoobot codex-login
```

Signs in with Codex / ChatGPT OAuth and saves the auth file path in `~/.faltoobot/config.toml`.

To change other settings, edit `~/.faltoobot/config.toml` directly.

## Terminal chat

You can also use Faltoobot locally in the terminal.

### Interactive mode

Run `faltoochat` with no prompt to open the terminal UI:

```bash
faltoochat
faltoochat --workspace=./repo
faltoochat --new-session
```

### One-shot mode

Run `faltoochat` with a prompt to execute a headless one-shot task in that workspace and print the final output to stdout:

```bash
faltoochat "draft a release note"
faltoochat "review unstaged files" --workspace=./repo --new-session
```

### Notify another chat

A one-shot `faltoochat` run can send its final output back to another chat key. This is useful for sub-agents, cron jobs, and detached background tasks:

```bash
faltoochat "List new emails for the user" --workspace=./emails --notify=code@main
```

## Commands inside chat

On WhatsApp:

```text
/help
/reset
/status
```

In terminal chat:

```text
/reset
/status
/tree
```

`faltoochat` supports image input, queued prompts while answering, and `Shift+Enter` for multiline input. Paste an image file path or use `Ctrl+V` to attach the current macOS clipboard image. WhatsApp chats now also support incoming image messages, including captioned images, image-only prompts, and multi-image albums.

## Development

Run the Astro docs site locally:

```bash
cd website
npm install
npm run dev
```

Build the static site locally:

```bash
cd website
npm run build
```

## Tests

Run the full test suite with coverage:

```bash
uv run pytest -n auto --cov=faltoobot --cov-report=term-missing:skip-covered
```

Coverage is published in the badge above, and pre-commit enforces a minimum of **78%** line coverage.

Need more details? See `docs/cli.md` and the Astro docs site in `website/`.
