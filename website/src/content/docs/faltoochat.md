---
title: faltoochat
description: Start the terminal coding chat and review mode.
order: 3
---

## What it is

`faltoochat` is the local terminal chat. It keeps project sessions, can use local tools, and includes review mode for git changes.

## Getting started

Install the package:

```bash
uv tool install faltoobot
```

Open chat in a project:

```bash
faltoochat
```

Start with a prompt immediately:

```bash
faltoochat "draft a release note"
```

## Review mode

Use review mode to:

- inspect changed files as tabs
- navigate diffs quickly
- write review comments
- stage or unstage selected lines
- send the review context back into chat

Install `ripgrep` too if you want faster project search inside review mode.
