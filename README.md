# faltoobot

[![tests](https://img.shields.io/github/actions/workflow/status/pratyushmittal/FaltooBot/main.yml?branch=main&label=tests)](https://github.com/pratyushmittal/FaltooBot/actions/workflows/main.yml)
![coverage](https://img.shields.io/badge/coverage-78%25-brightgreen)

`faltoobot` is a personal assistant that lives on its own WhatsApp account.

## How it works

- Get a separate SIM / WhatsApp account for Faltoobot.
- Sign in to that account on a spare phone.
- Install `faltoobot` on a computer that will stay online.
- Run `faltoobot auth` and scan the QR code from that phone.
- Message that WhatsApp number from your own number.

## Usage

### Install

```bash
uv tool install faltoobot
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

Then you can run `faltoobot` and `faltoochat` from any folder.

If uv asks you to add its tool bin directory to your `PATH`, run:

```bash
uv tool update-shell
```

### Configure

```bash
faltoobot configure
```

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
allowed_chats = ["15551234567@s.whatsapp.net"]
system_prompt = "You are Faltoobot, a concise and helpful AI assistant replying inside WhatsApp. Keep replies practical and readable on mobile."
```

If `oauth` is set, Faltoobot prefers that OAuth auth file over `api_key`. If `oauth` is blank, Faltoobot falls back to `OPENAI_API_KEY` from the environment.

Set `allowed_chats` to your own WhatsApp JID or phone number to keep the bot private. Leave it empty only if you want Faltoobot to reply to anyone who can message that account.

### Pair once

Pair the WhatsApp account once:

```bash
faltoobot auth
```

### Sign in to OpenAI Codex

For `faltoochat`, you can also bootstrap a Codex OAuth login directly. This saves Faltoobot's own auth file under `~/.faltoobot/auth.json` by default and writes that path into `openai.oauth` in your config:

```bash
faltoobot login
```

### Run in background

Install Faltoobot as a background service:

```bash
faltoobot install
```

Check whether it is running:

```bash
faltoobot status
```

Watch logs live:

```bash
faltoobot logs -f
```

Notes:
- macOS installs a `launchd` agent.
- Linux installs a `systemd --user` service.
- If you want the Linux service to stay up after logout, enable lingering for your user.

### Run in foreground

If you want to run it in the current terminal instead:

```bash
faltoobot run
```

### Terminal chat

You can also use Faltoobot locally in the terminal:

```bash
faltoochat
faltoochat "draft a release note"
faltoochat --new-session
```

### Commands

On WhatsApp:

```text
/help
/reset
```

In terminal chat:

```text
/tree
/reset
```

`faltoochat` supports image input, queued prompts while answering, and `Shift+Enter` for multiline input. Paste an image file path or use `Ctrl+V` to attach the current macOS clipboard image. WhatsApp chats now also support incoming image messages, including captioned images, image-only prompts, and multi-image albums.

### Development

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

### Tests

Run the full test suite with coverage:

```bash
uv run pytest -n auto --cov=faltoobot --cov-report=term-missing:skip-covered
```

Coverage is published in the badge above, and pre-commit enforces a minimum of **78%** line coverage.

### Update

```bash
uv tool upgrade faltoobot
```

Need more details? See the Astro docs site in `website/`.
