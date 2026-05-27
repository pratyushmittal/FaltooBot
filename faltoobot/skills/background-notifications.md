---
description: Set up background tasks, cron jobs, monitors, or sub-agents that notify this chat when they finish. Use this for reminders, scheduled digests, async work, and follow-up capable sub-agent tasks.
meta: disallow-sub-agent
---

Use this when work outside the current turn needs to send a message back into this chat later.

The unique identifier for your current chat is `{chat_key}`.

## Which Command Should I Use?

Default to `faltoochat "<task>" --notify="{chat_key}"` when the background task needs AI, reasoning, research, coding, summarization, or may need follow-up.

Use `faltoobot notify "{chat_key}" ...` when a normal script already has the final message and no AI work is needed.

Do not wrap an already-composed monitor alert in `faltoochat --notify` just to make the agent echo it. That adds another LLM run, can mutate or drop the message, can surface stderr as a WhatsApp alert, and delays delivery. If the script has the exact message, send it with `faltoobot notify`.

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
- website monitoring where the agent itself produces the final interpretation
- research tasks
- scheduled help with user tasks

Bad `faltoochat --notify` use-cases:
- echoing or rephrasing a final message that a shell/Python monitor already generated
- piping deterministic script output through a prompt like "reply exactly with this text"
- replacing `faltoobot notify` in a script that already does parsing, dedupe, and formatting

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

## Practical Reminders

- Prefer `faltoochat --notify` for AI work and follow-up capable sub-agents.
- Prefer `faltoobot notify` for plain script output.
- Use `nohup ... &` when launching background work from the shell.
- Put `2>&1` before the pipe when you also want stderr to reach `faltoobot notify`.
- Include enough detail in the notification message so you can act on it when it arrives.
- Prefer notifications for async completion or monitoring events; prefer normal chat turns for immediate back-and-forth.
