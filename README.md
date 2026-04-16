# faltoobot

[![tests](https://img.shields.io/github/actions/workflow/status/pratyushmittal/FaltooBot/main.yml?branch=main&label=tests)](https://github.com/pratyushmittal/FaltooBot/actions/workflows/main.yml)
![coverage](https://img.shields.io/badge/coverage-78%25-brightgreen)

`faltoobot` is a personal assistant that lives on its own WhatsApp account.

## How it works

- Get a separate SIM / WhatsApp account for Faltoobot.
- Sign in to that account on a spare phone.
- Install `faltoobot` on a computer that will stay online.
- Run `faltoobot configure` to set up OpenAI and WhatsApp.
- Run `faltoobot whatsapp` to keep the bot running.
- Message that WhatsApp number from your own number.

## Install

```bash
uv tool install faltoobot
```

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

### 1. Configure

```bash
faltoobot configure
```

The configure flow shows a simple menu:
- Wizard
- WhatsApp
- Codex / OpenAI

The wizard is the default. It helps you:
- sign in with Codex / ChatGPT OAuth, or set an OpenAI API key
- choose model / thinking / transcription settings
- pair the WhatsApp account

Example `~/.faltoobot/config.toml`:

```toml
[openai]
api_key = "your_key_here"
oauth = ""
model = "gpt-5.4"
thinking = "high"
fast = false

[bot]
allow_groups = false
allow_group_chats = ["15551234567@s.whatsapp.net"]
allowed_chats = ["15551234567@s.whatsapp.net"]
system_prompt = "You are Faltoobot, a concise and helpful AI assistant replying inside WhatsApp. Keep replies practical and readable on mobile."
```

If `oauth` is set, Faltoobot prefers that OAuth auth file over `api_key`. If `oauth` is blank, Faltoobot falls back to `OPENAI_API_KEY` from the environment.

Set `allowed_chats` to your own WhatsApp JID or phone number to keep the bot private. Leave it empty only if you want Faltoobot to reply to anyone who can message that account.
If you enable groups, set `allow_group_chats` to the participant phone numbers / JIDs that should be allowed to talk to the bot inside groups. If it is empty, the bot will not reply in groups. Even for allowed people, the bot now replies in groups only when the message explicitly mentions the bot.

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

### `faltoobot configure`

```bash
faltoobot configure
```

Opens the setup menu and restarts the service if it is already installed.

### `faltoobot makemigrations`

```bash
faltoobot makemigrations
```

Dev-only command for creating release migration scripts inside this repo.

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
faltoochat "List new emails for the user" --workspace=./emails --notify-chat-key=code@main
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
