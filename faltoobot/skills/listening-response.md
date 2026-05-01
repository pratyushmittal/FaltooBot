---
description: Listen for notifications from background scripts, cron jobs, monitors, or sub-agents, then continue the conversation when they arrive. This is useful for reminders, recurring tasks, updates, and async sub-tasks.
meta: disallow-sub-agent
---

Use this when work outside the current turn needs to send a message back into this chat later.

The unique identifier for your current chat is `{chat_key}`.

## How Notifications Work

The basic interface is:

```bash
printf 'Morning news is ready.' | faltoobot notify "{chat_key}" --source="cron:morning-news"
```

You can also pass the message inline:

```bash
faltoobot notify "{chat_key}" "Morning news is ready." --source="cron:morning-news"
```

If you do not pass a message argument, `faltoobot notify` reads the message body from stdin. Use `--source` to tell the bot why the notification arrived, for example `cron:morning-news`, `sub-agent:log-review`, or `hn-monitor.py`.


## Cron Job Example

Send a morning news digest every day at 8 AM:

```cron
0 8 * * * cd /path/to/project && faltoochat "Get top news items" --workspace=./morning-news 2>&1 | faltoobot notify "{chat_key}" --source="cron:morning-news"
```

Other good cron use-cases:
- email digests
- deployment completion notifications
- heartbeat alerts
- monitor websites
- help user with their tasks

## Sub-Agent Example

`faltoochat` is an AI agent installed on the system. You give it a task in natural language, and it works on that task using AI / LLMs. It can also take `--workspace` to run the task from that folder. The workspace can be a relative path.

You can use `faltoochat` both for one-off sub-agent tasks and from cron jobs using natural-language prompts.

Spawned subtasks for PRs, reviews, and other coding tasks should prefer background/async execution instead of blocking the main thread.

You can run a background `faltoochat` task and forward its final output back into this chat through `faltoobot notify`.

For example, ask it to review a pull request in a separate workspace:

```bash
nohup sh -c 'faltoochat "Review this PR: https://github.com/some-org/some-repo/pull/1554 and summarize the main issues." --workspace=./pr-review --new-session 2>&1 | faltoobot notify "{chat_key}" --source="sub-agent:pr-review"' &
```

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

- Use `nohup ... &` when launching background work from the shell.
- Put `2>&1` before the pipe when you also want stderr to reach `faltoobot notify`.
- Include enough detail in the notification message so you can act on it when it arrives.
- Prefer notifications for async completion or monitoring events; prefer normal chat turns for immediate back-and-forth.
