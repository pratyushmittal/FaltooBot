Save tokens, write less code.
Prefer functions over classes.
Use typing. Ignore type-check using `# type: ignore` for minor things or unsupported features in `ty`

Infra:
- uv
- python3.13+
- textual + rich (for TUI)

Tests:
- Keep tests small.
- Prefer BDD for new tests.
- E2E tests instead of unit tests.
- Fix things only after you are able to reproduce the problem. We don't want band-aids all over the code.
- Use max timeout of 30000 (30s) for tests when using `run_shell_call` tool.

Operational lessons from self-observation:
- Do not log raw private WhatsApp/user content in operational logs. Prefer type, length, source, and status metadata; keep full content only in the intended session history/storage.
- For cron/watch scripts, avoid hard-coded home-specific paths and stale workspace virtualenvs. Resolve `python3`, `uv`, `faltoobot`, and `faltoochat` at runtime; validate required executables before scheduling.
- Cron/watch wrappers should use `flock`, write timestamped diagnostics, include a dry-run command in setup notes, and fail loudly when dependencies or browser startup are unavailable.
- Background monitors must distinguish "no matching update" from "monitor failed". Alert or log a structured health event for parser failures, timeouts, empty final assistant outputs, and repeated notification-send failures.
- Keep generated monitor/workspace artifacts bounded: periodically review large `messages.json`, HARs, screenshots, cloned repos, and venvs; prefer retention/archival over unbounded growth.
