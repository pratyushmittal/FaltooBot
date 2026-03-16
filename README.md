# faltoobot

WhatsApp-first LLM bot with a terminal chat UI.

## Usage

### Install

```bash
uv tool install faltoobot
```

Then run `faltoobot` and `faltoochat` from any folder.

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
model = "gpt-5.2"
thinking = "none"
fast = false
```

If `api_key` is left blank, Faltoobot falls back to `OPENAI_API_KEY` from the environment.

### Authenticate and run

Authenticate once:

```bash
faltoobot auth
```

Start the bot:

```bash
faltoobot run
```

Start a terminal chat session:

```bash
faltoobot chat
faltoobot chat --name "Scratchpad"
faltoochat
```

### Chat commands

```text
/help
/reset
/exit
```

`faltoochat` also supports image input. Paste an image file path, paste markdown like `![alt](path)`, or use `Ctrl+V` to attach the current macOS clipboard image.

### Update

```bash
uv tool upgrade faltoobot
```

### macOS service

```bash
faltoobot install
faltoobot status
faltoobot logs -f
```

## For Developers

### Set up the repo

```bash
git clone https://github.com/pratyushmittal/FaltooBot.git
cd FaltooBot
uv sync
uv run faltoobot configure
uv run faltoochat
```

### Publish updates

1. Bump the package version:

```bash
uv version --bump patch
```

2. Build the package:

```bash
uv build --no-sources
```

3. Publish to PyPI:

```bash
uv publish
```

Use a PyPI token via `UV_PUBLISH_TOKEN`, or publish with a configured trusted publisher.

More details: `docs/guide.md`
