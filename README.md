# faltoobot

A small WhatsApp-first LLM bot for WhatsApp.

## Quick setup

### 1. Install dependencies

```bash
uv sync
```

### 2. Create the config file

```bash
uv run faltoobot paths --config
```

This creates `~/.faltoobot/config.toml` if it does not exist.

Add your OpenAI key:

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

### 3. Authenticate WhatsApp

```bash
uv run faltoobot auth
```

Scan the QR code from WhatsApp:
- Settings
- Linked Devices
- Link a Device

### 4. Run the bot

```bash
uv run faltoobot run
```

Send yourself:

```text
!ai Say hello from faltoobot
```

### 5. Install as a background service on macOS

```bash
uv run faltoobot install
uv run faltoobot status
uv run faltoobot logs -f
```

## More docs

See `docs/guide.md` for:
- commands
- triggers
- config
- file layout
- development workflow
- code quality hooks
