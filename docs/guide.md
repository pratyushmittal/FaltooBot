# Faltoobot guide

## Overview

Phase 1 is a proof of concept with:
- async Python
- `uv`-first workflow
- CLI-first workflow
- WhatsApp session via `neonize`
- OpenAI for text generation
- macOS `launchd` service via `faltoobot install`

For the WhatsApp transport, this POC uses [`neonize`](https://github.com/krypton-byte/neonize), which is Python-friendly and async. It gives us a cleaner Phase 1 path than wrapping `wacli` as a long-running sidecar, especially because `wacli` is designed around single-process store locking.

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

## Config

The config file lives at `~/.faltoobot/config.toml`.

Example:

```toml
[openai]
api_key = "your_key_here"
model = "gpt-4.1-mini"
max_output_tokens = 700

[bot]
trigger_prefix = "!ai"
allow_groups = false
allowed_chats = []
max_history_messages = 12
max_output_chars = 6000
system_prompt = "You are Faltoobot, a concise and helpful AI assistant replying inside WhatsApp. Keep replies practical and readable on mobile."
```

## Allowlist

If you want to restrict who can use the bot, set:

```toml
[bot]
allowed_chats = ["9198xxxxxxx", "9197xxxxxxx@s.whatsapp.net"]
```

Numbers without `@...` are normalized to `@s.whatsapp.net`.

## Files used by the app

Everything lives under:

```text
~/.faltoobot
```

Important files:
- `~/.faltoobot/config.toml`
- `~/.faltoobot/session.db` — WhatsApp session data
- `~/.faltoobot/state.db` — Faltoobot memory + dedupe state
- `~/.faltoobot/faltoobot.log`
- `~/Library/LaunchAgents/com.faltoobot.agent.plist`

## Development

Common `uv` commands:

```bash
uv sync
uv sync --dev
uv lock
uv run faltoobot paths
uv run faltoobot auth
uv run faltoobot run
```

Add or remove dependencies with:

```bash
uv add <package>
uv add --dev <package>
uv remove <package>
```

## Code quality

Install the dev tools and git hooks:

```bash
uv sync --dev
uv run pre-commit install
```

Run them manually anytime:

```bash
uv run pre-commit run --all-files
uv run ruff check .
uv run ruff format .
```

The repo currently uses:
- `ruff` for linting and formatting
- `pre-commit` for local git hooks

## Notes

- Phase 1 is text-only.
- Groups are off by default.
- The bot ignores messages sent by itself.
- This repo is meant to be used with `uv` for Python dependency management.
- Configuration lives in `config.toml`, not `.env` files.
- `faltoobot install` writes a `launchd` runner that uses `uv run faltoobot run`.
- This repo currently implements `install` as a macOS-only command.

## Future phase ideas

- support multiple model providers
- media understanding
- allow voice notes
- contact routing / policies
- better prompt templates
- admin commands from WhatsApp
- webhooks / observability
