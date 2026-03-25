Save tokens, write less code.
Prefer functions over classes.
Use typing. Ignore type-check using `# type: ignore` for minor things or unsupported features in `ty`

Infra:
- uv
- python3.13+
- textual + rich (for TUI)

Tests:
- Keep tests small.
- E2E tests instead of unit tests.
- Fix things only after you are able to reproduce the problem. We don't want band-aids all over the code.
- Use max timeout of 30000 (30s) for tests when using `run_shell_call` tool.
