# faltoobot

A small WhatsApp-first LLM bot.

Phase 1 is a proof of concept:
- async Python
- `uv`-first workflow
- CLI-first workflow
- WhatsApp session via `neonize`
- OpenAI for text generation
- macOS `launchd` service via `faltoobot install`

## Why this shape

You wanted an async Python implementation and a CLI like:
- `faltoobot install`
- `faltoobot logs`

For the WhatsApp transport, this POC uses [`neonize`](https://github.com/krypton-byte/neonize), which is Python-friendly and async. It gives us a cleaner Phase 1 path than wrapping `wacli` as a long-running sidecar, especially because `wacli` is designed around single-process store locking.

## What Phase 1 does

- Authenticates a WhatsApp linked device from the terminal
- Listens for incoming WhatsApp messages
- Sends prompts to OpenAI
- Replies back on WhatsApp
- Keeps lightweight per-chat memory in SQLite
- Supports a simple macOS background service

## Commands

- `faltoobot auth` — authenticate the WhatsApp session by scanning a QR code
- `faltoobot run` — run the bot in the foreground
- `faltoobot install` — install and start the macOS `launchd` service
- `faltoobot uninstall` — remove the macOS service
- `faltoobot status` — show service status
- `faltoobot logs` — view logs
- `faltoobot paths` — print important paths

## Trigger format

By default the bot only responds to messages that start with:

```text
!ai
```

Examples:

```text
!ai Explain MCP in 5 bullets
!ai Draft a polite follow-up for this client
!ai Summarize this idea: ...
```

Built-in local commands:

```text
!help
!reset
```

- `!help` shows usage
- `!reset` clears chat memory for the current chat

## Quick start

### 1. Install

```bash
uv sync
```

### 2. Create the env file

```bash
uv run faltoobot paths --env
```

This creates `~/.faltoobot/.env` if it does not exist.

Add your OpenAI key there:

```env
OPENAI_API_KEY=your_key_here
FALTOOBOT_OPENAI_MODEL=gpt-4.1-mini
FALTOOBOT_TRIGGER_PREFIX=!ai
FALTOOBOT_ALLOW_GROUPS=false
FALTOOBOT_ALLOWED_CHATS=
FALTOOBOT_MAX_HISTORY_MESSAGES=12
FALTOOBOT_MAX_OUTPUT_CHARS=6000
FALTOOBOT_MAX_OUTPUT_TOKENS=700
FALTOOBOT_SYSTEM_PROMPT=You are Faltoobot, a concise and helpful AI assistant replying inside WhatsApp. Keep replies practical and readable on mobile.
```

### 3. Authenticate WhatsApp

```bash
uv run faltoobot auth
```

That will print a QR code in the terminal. Scan it from WhatsApp:

- WhatsApp
- Settings
- Linked Devices
- Link a Device

### 4. Run in foreground

```bash
uv run faltoobot run
```

Now send yourself:

```text
!ai Say hello from faltoobot
```

### 5. Install as a service on macOS

```bash
uv run faltoobot install
uv run faltoobot status
uv run faltoobot logs -f
```

## Allowlist

If you want to restrict who can use the bot, set:

```env
FALTOOBOT_ALLOWED_CHATS=9198xxxxxxx,9197xxxxxxx@s.whatsapp.net
```

Numbers without `@...` are normalized to `@s.whatsapp.net`.

## Files used by the app

Everything lives under:

```text
~/.faltoobot
```

Important files:
- `~/.faltoobot/.env`
- `~/.faltoobot/session.db` — WhatsApp session data
- `~/.faltoobot/state.db` — Faltoobot memory + dedupe state
- `~/.faltoobot/faltoobot.log`
- `~/Library/LaunchAgents/com.faltoobot.agent.plist`

## Notes

- Phase 1 is text-only.
- Groups are off by default.
- The bot ignores messages sent by itself.
- This repo is meant to be used with `uv` for Python dependency management.
- `faltoobot install` writes a `launchd` runner that uses `uv run faltoobot run`.
- This repo currently implements `install` as a macOS-only command.

## Development

Common `uv` commands:

```bash
uv sync
uv lock
uv run faltoobot paths
uv run faltoobot auth
uv run faltoobot run
```

Add or remove dependencies with:

```bash
uv add <package>
uv remove <package>
```

## Future phase ideas

- support multiple model providers
- media understanding
- allow voice notes
- contact routing / policies
- better prompt templates
- admin commands from WhatsApp
- webhooks / observability
