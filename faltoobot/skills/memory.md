---
name: memory
description: Use this skill when the user explicitly asks you to remember, forget, update memory for future chats.
---
The user is managing persistent memory which survives /reset command.

Memory file for this chat:
`{memory_file}`

Rules:
- Only save memory when the user explicitly asks.
- Keep memory short, reusable, and future-relevant.
- Prefer stable preferences, facts, and standing instructions.
- Do not save secrets, passwords, API keys, OTPs, or other highly sensitive data.
- Do not save one-off temporary details unless the user clearly wants them kept.
- Store one memory per line as a markdown bullet: `- memory text`

Use the `run_shell_call` tool to inspect or update the memory file.
Prefer a short `python - <<'PY'` script over `echo`, `sed`, or fragile shell quoting when editing the file.

When saving memory:
1. read the current file if needed
2. avoid duplicates
3. append one new bullet if the memory is new

When updating or correcting old memory:
1. inspect the current memory file
2. remove outdated memory
3. append the corrected memory

When forgetting memory:
- delete the matching bullet
- if the delete target is ambiguous, ask a short clarification question

When the user asks what you remember:
- inspect the memory file if needed
- summarize the saved bullets clearly

After a successful memory change, tell the user what you saved, removed, or updated.
