# faltoobot

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
model = "gpt-5.4"
thinking = "high"
fast = false

[bot]
allow_groups = false
allowed_chats = ["15551234567@s.whatsapp.net"]
system_prompt = "You are Faltoobot, a concise and helpful AI assistant replying inside WhatsApp. Keep replies practical and readable on mobile."
```

If `api_key` is left blank, Faltoobot falls back to `OPENAI_API_KEY` from the environment.

Set `allowed_chats` to your own WhatsApp JID or phone number to keep the bot private. Leave it empty only if you want Faltoobot to reply to anyone who can message that account.

### Pair once

Pair the WhatsApp account once:

```bash
faltoobot auth
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
faltoobot chat
faltoobot chat --name "Scratchpad"
faltoochat
```

### Commands

On WhatsApp:

```text
/help
/reset
```

In terminal chat:

```text
/help
/reset
/exit
```

`faltoochat` also supports image input. Paste an image file path, paste markdown like `![alt](path)`, or use `Ctrl+V` to attach the current macOS clipboard image.

### Tests

Run the test suite (parallel):

```bash
uv run pytest -n auto
```

### Update

```bash
uv tool upgrade faltoobot
```

Need more details? See `docs/guide.md`.
