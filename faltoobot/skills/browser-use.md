---
description: Using Playwright for browser use and persistent sessions. Use this when you need to open JS heavy websites in browser, such as X/Twitter, Instagram, or Booking.com. Also useful when you want to reuse the logged in session of the user in the browser.
---

Use the shared FaltooBot browser for login-sensitive sites. Prefer `run_shell_call` for browser work.

Runtime value:
- CDP URL: `{cdp_url}`

Important:
- First try `connect_over_cdp("{cdp_url}")`.
- If CDP is not running, start `faltoobot browser` and then connect over CDP.
- Do not start Chrome directly with the profile path; use `faltoobot browser` so the normal keychain/login state is available.
- Do not use Playwright `launch_persistent_context(...)` with the shared profile. Playwright launches Chrome with automation/keychain flags that can make saved logins appear missing.
- Do not launch a headless browser against the shared profile for login-sensitive sites.
- Do not create `browser.new_context()` for login-sensitive sites; use the existing CDP context so cookies and local storage are shared.
- Keep the shared browser open unless the user explicitly asks to close it.

Example with `run_shell_call`:

```bash
uv run --with playwright python - <<'PY'
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request
from playwright.sync_api import sync_playwright

cdp_url = "{cdp_url}"
url = "https://example.com"
screenshot = Path("browser-home.png")


def cdp_ready() -> bool:
    try:
        urllib.request.urlopen(f"{cdp_url}/json/version", timeout=2).read()
        return True
    except Exception:
        return False


if not cdp_ready():
    faltoobot = shutil.which("faltoobot") or str(Path.home() / ".local/bin/faltoobot")
    subprocess.Popen(
        [faltoobot, "browser"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(30):
        if cdp_ready():
            break
        time.sleep(1)
    else:
        raise RuntimeError("FaltooBot browser did not become ready")

with sync_playwright() as playwright:
    browser = playwright.chromium.connect_over_cdp(cdp_url)
    if not browser.contexts:
        raise RuntimeError("Connected browser has no reusable login context")
    context = browser.contexts[0]
    page = context.new_page()
    page.goto(url, wait_until="domcontentloaded")
    page.screenshot(path=str(screenshot), full_page=True)
    print(page.title())
    print(screenshot)
```

Use `load_image` tool for seeing saved screenshots.

If the website requires 2FA or user's login, ask them to run `faltoobot browser` on their machine and complete the login there. After that, reconnect to the same CDP URL.

Screenshot guidance:
- save screenshots inside the current `workspace` directory
- prefer screenshots before complex interactions or when inspecting layouts
- also print useful text/DOM state to stdout so the model has both visual and textual context

Useful patterns:
- open JS-heavy websites that need a real browser
- reuse the user's logged-in session for websites that block simple scraping
- inspect pages visually with screenshots while also printing useful page state
