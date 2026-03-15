# faltoobot

WhatsApp bot for LLMs.

## Setup

```bash
git clone https://github.com/pratyushmittal/FaltooBot.git
cd FaltooBot
uv sync
uv run faltoobot configure
```

Configure `~/.faltoobot/config.toml` interactively:

```toml
[openai]
api_key = "your_key_here"
model = "gpt-5.2"
thinking = "none"
```

You can rerun `uv run faltoobot configure` anytime to update the file.
If `api_key` is left blank, Faltoobot falls back to `OPENAI_API_KEY` from the environment.

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

`faltoochat` also supports image input. Paste an image file path, paste markdown like `![alt](path)`, or use `Ctrl+V` to attach the current macOS clipboard image.

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
