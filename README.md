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
model = "gpt-5.4"
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

Start a new CLI session:

```bash
uv run faltoobot chat
uv run faltoobot chat --name "Scratchpad"
uv run faltoochat
```

## Use

Send messages like:

```text
Say hello
/help
/reset
```

In CLI chat:

```text
/help
/reset
/exit
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
