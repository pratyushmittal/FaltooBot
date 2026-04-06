---
name: scheduled-subagents
description: Guide to using `faltoochat` CLI. `faltoochat` is a powerful CLI command for background tasks, recurring monitors, reminders, summaries, nudges, and sub-agents.
---

`faltoochat` is a powerful coding agent accessible from the command line. It can use bash, browser, and search tools inside its workspace. You are also an instance of `faltoochat`.

When run without a prompt, `faltoochat` opens the interactive terminal UI. When run with a prompt, it performs a one-shot task, prints the final output to stdout / stderr, and exits.

```bash
faltoochat "What's new on Hacker News?"
```

The above command runs a one-shot task using the current directory as its workspace.

To use a separate workspace, provide `--workspace`. It can be a relative or absolute path. The path will be created automatically if it does not exist.

```bash
faltoochat "Create a one-page documentation for this repo: github.com/some-repo" --workspace=./documentation

# the above is similar to
mkdir -p documentation && cd documentation && faltoochat "Create a one-page documentation for this repo: github.com/some-repo"
```

Sessions continue from earlier chat history. To start a fresh chat without previous history, use `--new-session`:

```bash
faltoochat "Review unstaged files" --new-session
```

The output of all the above commands is printed to stdout / stderr.

## Using `faltoochat` as a sub-agent

The real power of `faltoochat` comes from using it as a sub-agent. You will often use it for this purpose.

While `workspace` is the directory where `faltoochat` performs its tasks, the chat history for that session is maintained by `chat_key`.

The `chat_key` of your ongoing chat with the user is `{chat_key}`.

You will often want the output of the `faltoochat` command to be sent back to you. You can do this by providing `--notify-chat-key`.

```bash
faltoochat "Give me a list of new emails for the user." --notify-chat-key={chat_key} --workspace=./emails
```

Once the above command fetches new emails, it will send the output back to your current chat. The output will be added to your current chat in this format:

```text
# Response from sub-agent (not visible to user)

message: Give me a list of new emails for the user.
workspace: ./emails

## output
There are 2 new emails:
1. Foobar
2. Foobaz
```

Note: even though the `role` will be `user` for the above output from the sub-agent, it is not a message typed by the user. It is the output provided by the sub-agent.

## Using `faltoochat` sub-agent for cron jobs

The above capability of using `faltoochat` as a sub-agent and relaying the output back to a chat is very useful with cron jobs.

Example:

```cron
0 8 * * * faltoochat "Get top news for today" --notify-chat-key={chat_key} --workspace=./top-news/
```

You can add the above cron job to get the top news at 8 AM every day. Based on this notification, you can then reply back to the user with the top news relevant to them.

## Running `faltoochat` as a background job

You can also start `faltoochat` as a background job without using cron. This is useful for fire-and-forget tasks started from `run_shell_call` or from a terminal.

When starting it from `run_shell_call`, make sure the command is fully detached. Redirect stdout / stderr to a log file and run it with `nohup` in the background.

```bash
nohup faltoochat "Research the best weekend train options from Lucknow to Delhi and send me a summary" --notify-chat-key={chat_key} --workspace=./delhi-trip > ./delhi-trip/job.log 2>&1 &
```

This returns control to the current agent quickly while the background job keeps running. Use cron when you want a true recurring schedule. Use a detached background job when you want to start something now. It is suitable for one-time tasks.
