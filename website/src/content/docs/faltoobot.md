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


## Opt-in child voice replies

Child-friendly TTS replies are disabled by default. To allow them in one direct chat, add that phone number or JID to both `allowed_chats` and `voice_reply_chats` in `~/.faltoobot/config.toml`:

```toml
[bot]
allowed_chats = ["15551234567"]
voice_reply_chats = ["15551234567"]
```

Set `OPENAI_API_KEY` in the service environment. When an opted-in incoming voice note is clearly from a young child, Faltoobot can generate a short Hindi, Hinglish, or English reply with the OpenAI `gpt-4o-mini-tts` speech model and send it as a WhatsApp PTT voice note. Adult voice notes stay text by default. The first generated voice reply includes a one-time AI-voice disclosure, and any TTS failure falls back to text.
