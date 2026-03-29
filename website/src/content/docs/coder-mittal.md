---
title: Coder Mittal
description: Start the terminal coding agent and use review mode.
order: 3
---

## What it is

Coder Mittal is the local coding member of the family. It runs in the terminal, keeps session history, uses local tools, and can review git changes before you send follow-up prompts.

## Current command

Today this guide maps to the existing `faltoochat` binary.

## Getting started

Install the current package:

```bash
uv tool install faltoobot
```

Launch the coding chat:

```bash
faltoochat
```

Start with a prompt immediately:

```bash
faltoochat "draft a release note"
```

## Why it is different

The differentiator is **review mode**:
- inspect changed files as tabs
- navigate diffs quickly
- add review comments
- stage or unstage selected lines
- send review feedback back into chat in one shot

## Useful commands

```text
/tree
/reset
```

Install `ripgrep` too if you want faster project search inside review mode.
