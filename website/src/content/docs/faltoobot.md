---
title: faltoobot
description: Run the WhatsApp AI bot.
order: 2
---

## What it is

`faltoobot` runs the WhatsApp side of faltoo.bot. Give it a WhatsApp account and it can answer chats with your configured OpenAI model.

## Getting started

Install the package:

```bash
uv tool install faltoobot
```

Create or refresh the config:

```bash
faltoobot update
```

Log in to WhatsApp:

```bash
faltoobot whatsapp-login
```

Run the bot:

```bash
faltoobot run
```

## Good fit

Use `faltoobot` when you want an AI contact for:

- quick replies and rewrites
- voice note or image-aware help
- generated images sent back to WhatsApp
- background notifications from scripts and agents
