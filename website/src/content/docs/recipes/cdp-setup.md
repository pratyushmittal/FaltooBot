---
title: CDP setup
description: A minimal server-first recipe for running a browser with CDP and handling browser tasks.
order: 41
---

## Why this recipe exists

We made this recipe for tasks that need a real browser on the server.

The goal is simple:
- launch a browser with CDP
- inspect the page
- complete the task
- keep visible browsers open for follow-up work

## Core idea

CDP works when the browser is launched with:
- `--remote-debugging-port=PORT`
- `--user-data-dir=/some/non-default/profile`

Then you can inspect it through:

```bash
http://127.0.0.1:PORT/json/version
http://127.0.0.1:PORT/json/list
```

## Files used in this workspace

Skill path:

```bash
skills/cdp-browser-control
```

Inside that skill:

```bash
skills/cdp-browser-control/launch_chrome_cdp.sh
skills/cdp-browser-control/launch_visible_cdp_rustdesk.sh
skills/cdp-browser-control/prepare_shared_chrome_for_testing.sh
skills/cdp-browser-control/probe_cdp.py
skills/cdp-browser-control/connect_playwright_cdp.py
```

## Preferred flow

1. If a remote desktop is available, launch a visible browser.
2. Otherwise launch a headless browser.
3. Verify the CDP endpoint.
4. Attach a helper only if needed.
5. Do not close a live visible browser unless you explicitly want that.

## Visible browser on the remote desktop

Use this when you want to watch the task live.

```bash
cd skills/cdp-browser-control
./prepare_shared_chrome_for_testing.sh
URL='https://example.com' PORT=9223 ./launch_visible_cdp_rustdesk.sh
python3 probe_cdp.py --endpoint http://127.0.0.1:9223
```

This is the preferred flow for tasks like:
- opening a target site
- inspecting page state
- logging in
- completing a browser task step by step

## Headless browser on the server

Use this when you do not need a visible desktop.

```bash
cd skills/cdp-browser-control
./launch_chrome_cdp.sh \
  --browser chrome-testing \
  --headless \
  --port 9222 \
  --user-data-dir ~/.cache/faltoobot/cdp-headless-profile
python3 probe_cdp.py --endpoint http://127.0.0.1:9222
```

## Attach a helper when needed

```bash
cd skills/cdp-browser-control
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python connect_playwright_cdp.py --endpoint http://127.0.0.1:9222 --url https://example.com
```

Important behavior in this workspace:
- attached visible browsers should stay open
- the helper should detach without closing the browser by default

## Good defaults

Use:

```bash
--user-data-dir=/home/remote/.cache/faltoobot/some-profile
--password-store=basic
--disable-gpu
--disable-dev-shm-usage
```

## Quick checks

```bash
python3 probe_cdp.py --endpoint http://127.0.0.1:9222
python3 probe_cdp.py --endpoint http://127.0.0.1:9223
```

Useful HTTP endpoints:
- `http://127.0.0.1:9222/json/version`
- `http://127.0.0.1:9222/json/list`

## If something breaks

- use a fresh `--user-data-dir`
- switch ports
- prefer Chrome for Testing on the server
- if keyring prompts appear, use `--password-store=basic`

## In one line

Launch a browser with CDP, verify the endpoint, use the visible remote-desktop flow when you want to inspect the task live, and keep the setup as small as possible.
