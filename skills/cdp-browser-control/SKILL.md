---
name: cdp-browser-control
description: Minimal CDP browser setup for server-side browser tasks with a visible or headless Chromium browser.
keywords: ["cdp", "browser", "chrome", "automation", "server", "playwright"]
---
Use this skill when a task needs a real browser on the server.

Keep it minimal. The goal is not a huge browser framework. The goal is to get a usable Chromium browser running, expose CDP, inspect the page, and complete the task.

Use this for tasks like:
- open a site and complete a browser task
- open a site, log in, and inspect state
- attach to a running visible browser on the remote desktop
- take a screenshot before interacting

Preferred flow:
1. If a remote desktop is available, launch a visible browser with `launch_visible_cdp_rustdesk.sh`.
2. Otherwise launch a headless browser with `launch_chrome_cdp.sh --headless`.
3. Verify the endpoint with `probe_cdp.py`.
4. Attach a helper only if needed.
5. Leave visible user-facing browsers open unless the user explicitly asks to close them.

Rules:
- prefer one dedicated `--user-data-dir` per task
- use `--password-store=basic`
- on servers, prefer stable low-risk flags like `--disable-gpu` and `--disable-dev-shm-usage`
- inspect first, then interact
- keep the setup focused on the task, not on extra platform-specific variations

Files:
- `launch_chrome_cdp.sh` - launch a local or headless Chromium browser with CDP
- `launch_visible_cdp_rustdesk.sh` - launch a visible browser in the `remote` desktop session
- `probe_cdp.py` - verify `/json/version` and `/json/list`
- `connect_playwright_cdp.py` - attach Playwright to an existing CDP browser without closing it by default
- `prepare_shared_chrome_for_testing.sh` - prepare a shared Chrome for Testing binary for the server desktop flow
- `recipes.md` - minimal server-first setup examples
