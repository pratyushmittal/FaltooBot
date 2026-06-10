---
description: Set up background tasks, cron jobs, monitors, or sub-agents that notify this chat when they finish. Use this for reminders, scheduled digests, async work, and follow-up capable sub-agent tasks.
meta: disallow-sub-agent
---

Use this when work outside the current turn needs to send a message back into this chat later.

The unique identifier for your current chat is `{chat_key}`.

## Which Command Should I Use?

Default to `faltoochat "<task>" --notify="{chat_key}"` when the background task needs AI, reasoning, research, coding, summarization, or may need follow-up.

Use `faltoobot notify "{chat_key}" ...` when a normal script already has the final message and no AI work is needed. Do not use `faltoochat --notify` just to echo deterministic script output.

## AI Cron / Sub-Agent Tasks

`faltoochat` is an AI agent installed on the system. You give it a task in natural language, and it works on that task using AI / LLMs. It can also take `--workspace` to run the task from that folder. The workspace can be a relative path.

Use `faltoochat --notify` for one-shot sub-agent tasks so the result, stderr, and sub-agent follow-up id are sent back to this chat.

Send a morning news digest every day at 8 AM:

```cron
0 8 * * * cd /path/to/project && faltoochat "Prepare a morning news digest" --workspace=./morning-news --notify="{chat_key}" --source="cron:morning-news"
```

Spawned subtasks for PRs, reviews, and other coding tasks should prefer background/async execution instead of blocking the main thread.

For example, ask it to review a pull request in a separate workspace:

```bash
nohup faltoochat "Review this PR: https://github.com/some-org/some-repo/pull/1554 and summarize the main issues." --workspace=./pr-review --new-session --notify="{chat_key}" --source="sub-agent:pr-review" &
```

If a notification includes `sub-agent follow-up id: <session_id>`, continue that sub-agent conversation with:

```bash
faltoochat "<follow-up question>" --session-id=<session_id> --notify="{chat_key}"
```

If a `faltoochat --notify` error looks transient, ask the same sub-agent to retry with the same `--session-id`.

Good `faltoochat --notify` use-cases:
- email/news digests that need summarization
- PR reviews and code investigations
- website monitoring where the agent produces the final interpretation
- research tasks
- scheduled help with user tasks

## Plain Script Notifications

Use `faltoobot notify` when a script already has the final message and only needs to deliver it into this chat.

```bash
printf 'Morning news is ready.' | faltoobot notify "{chat_key}" --source="cron:morning-news"
```

You can also pass the message inline:

```bash
faltoobot notify "{chat_key}" "Morning news is ready." --source="cron:morning-news"
```

If you do not pass a message argument, `faltoobot notify` reads the message body from stdin. Use `--source` to tell the bot why the notification arrived, for example `cron:backup`, `deploy:production`, or `hn-monitor.py`.

Good `faltoobot notify` use-cases:
- deployment completion notifications
- heartbeat alerts
- backup success/failure messages
- plain scripts that already generated the exact message

## Python API Example

For Python scripts, use `uv run --with faltoobot` so the script has the package available without adding project dependencies:

```bash
uv run --with faltoobot python - <<'PY'
from faltoobot import notify_queue

notify_queue.enqueue_notification(
    "{chat_key}",
    "Maintenance job finished successfully.",
    source="script:maintenance",
)
PY
```


## Durable Cron Script Practices

When you create or edit a cron-launched shell/Python monitor, make it portable across user/home migrations and package reinstalls:

- Do not hard-code machine-specific paths such as `/home/exedev/...` or a workspace `.venv/bin/python` created on another host.
- Resolve CLIs at runtime, e.g. `FALTOOBOT_BIN="${FALTOOBOT_BIN:-$(command -v faltoobot)}"`, and fail with a clear message if missing.
- Prefer `python3` or `uv run --with <packages> python` for cron scripts unless a local virtualenv is actively managed by that same script.
- If using a local `.venv`, validate that its interpreter is executable before each run and either rebuild it or fall back explicitly; broken absolute symlinks should not silently break recurring jobs.
- Log startup diagnostics for long-lived monitors: resolved Python, resolved `faltoobot`, working directory, and whether this is a dry run. Keep diagnostics free of secrets.
- Use `flock` or another lock so overlapping slow runs do not pile up.

Robust wrapper skeleton:

```bash
#!/usr/bin/env bash
set -euo pipefail
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$BASE_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$BASE_DIR/.venv/bin/python"
  else
    PYTHON_BIN="$(command -v python3 || true)"
  fi
fi
FALTOOBOT_BIN="${FALTOOBOT_BIN:-$(command -v faltoobot || true)}"
if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
  echo "Missing usable Python interpreter" >&2
  exit 1
fi
if [[ -z "$FALTOOBOT_BIN" || ! -x "$FALTOOBOT_BIN" ]]; then
  echo "Missing faltoobot executable" >&2
  exit 1
fi
```

## Practical Reminders

- Prefer `faltoochat --notify` for AI work and follow-up capable sub-agents.
- Prefer `faltoobot notify` for plain script output.
- Use `nohup ... &` when launching background work from the shell.
- Put `2>&1` before the pipe when you also want stderr to reach `faltoobot notify`.
- Include enough detail in the notification message so you can act on it when it arrives.
- Prefer notifications for async completion or monitoring events; prefer normal chat turns for immediate back-and-forth.
