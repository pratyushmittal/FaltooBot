---
title: Start here
description: Install faltoo.bot and pick the command you need.
order: 1
---

## Install

```bash
uv tool install faltoobot
```

That installs both commands:

- `faltoobot` — the WhatsApp AI bot
- `faltoochat` — the terminal coding chat and review tool

## Pick your guide

- [Set up faltoobot](/docs/faltoobot/)
- [Set up faltoochat](/docs/faltoochat/)
- [Recipes](/docs/recipes/)

## Configure OpenAI

Create or update the config:

```bash
faltoobot update
```

Then edit `~/.faltoobot/config.toml` or run Codex / ChatGPT OAuth login if that is your preferred auth path.
