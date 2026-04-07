# CDP browser recipes

This skill is meant for simple server-side browser work.

The minimum idea is:
1. launch a browser with CDP
2. verify the endpoint
3. attach only if needed
4. keep visible browsers open

## 1) Visible browser on the remote desktop

Use this when you want to watch the task live.

```bash
cd skills/cdp-browser-control
./prepare_shared_chrome_for_testing.sh
URL='https://example.com' PORT=9223 ./launch_visible_cdp_rustdesk.sh
python3 probe_cdp.py --endpoint http://127.0.0.1:9223
```

This is the preferred flow for tasks like:
- open the target site
- inspect the page visually
- complete the task
- keep the browser open for follow-up steps

## 2) Headless browser on the server

Use this when a visible desktop is not needed.

```bash
cd skills/cdp-browser-control
./launch_chrome_cdp.sh \
  --browser chrome-testing \
  --headless \
  --port 9222 \
  --user-data-dir ~/.cache/faltoobot/cdp-headless-profile
python3 probe_cdp.py --endpoint http://127.0.0.1:9222
```

## 3) Attach a helper when needed

```bash
cd skills/cdp-browser-control
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python connect_playwright_cdp.py --endpoint http://127.0.0.1:9222 --url https://example.com
```

Important:
- attached visible browsers should stay open
- do not close the live browser unless the user explicitly asks

## 4) Good defaults

Use:

```bash
--user-data-dir=/home/remote/.cache/faltoobot/some-profile
--password-store=basic
--disable-gpu
--disable-dev-shm-usage
```

## 5) Quick checks

```bash
python3 probe_cdp.py --endpoint http://127.0.0.1:9222
python3 probe_cdp.py --endpoint http://127.0.0.1:9223
```

Useful HTTP endpoints:
- `http://127.0.0.1:9222/json/version`
- `http://127.0.0.1:9222/json/list`

## 6) If something breaks

- use a fresh `--user-data-dir`
- switch ports
- prefer Chrome for Testing on the server
- if keyring prompts appear, use `--password-store=basic`
