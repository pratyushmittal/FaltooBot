# faltoobot

WhatsApp bot for LLMs.

## Setup

```bash
git clone https://github.com/pratyushmittal/FaltooBot.git
cd FaltooBot
uv sync
uv run faltoobot paths --config
```

Edit `~/.faltoobot/config.toml`:

```toml
[openai]
api_key = "your_key_here"
model = "gpt-4.1-mini"

[bot]
trigger_prefix = "!ai"
```

## Run

Authenticate once:

```bash
uv run faltoobot auth
```

Start the bot:

```bash
uv run faltoobot run
```

## Use

Send messages like:

```text
!ai Say hello
!help
!reset
```

## Update

```bash
uv run faltoobot update
```

## macOS service

```bash
uv run faltoobot install
uv run faltoobot status
uv run faltoobot logs -f
```

More details: `docs/guide.md`
