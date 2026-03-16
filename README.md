# faltoobot

WhatsApp bot for LLMs.

## Install

Install the CLI globally with uv:

```bash
uv tool install faltoobot
```

Then you can run `faltoobot` and `faltoochat` from any folder.

If uv asks you to add its tool bin directory to your `PATH`, run:

```bash
uv tool update-shell
```

## Setup

Create your config:

```bash
faltoobot configure
```

Configure `~/.faltoobot/config.toml` interactively:

```toml
[openai]
api_key = "your_key_here"
model = "gpt-5.2"
thinking = "none"
fast = false
```

You can rerun `faltoobot configure` anytime to update the file.
If `api_key` is left blank, Faltoobot falls back to `OPENAI_API_KEY` from the environment.

## Run

Authenticate once:

```bash
faltoobot auth
```

Start the bot:

```bash
faltoobot run
```

Start a new CLI session:

```bash
faltoobot chat
faltoobot chat --name "Scratchpad"
faltoochat
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
uv tool upgrade faltoobot
```

## macOS service

```bash
faltoobot install
faltoobot status
faltoobot logs -f
```

## Development

```bash
git clone https://github.com/pratyushmittal/FaltooBot.git
cd FaltooBot
uv sync
uv run faltoobot configure
uv run faltoochat
```

More details: `docs/guide.md`
