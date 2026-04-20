---
description: Using Playwright for browser use and persistent sessions. Use this when you need to open JS heavy websites in browser, such as X/Twitter, Instagram, or Booking.com. Also useful when you want to reuse the logged in session of the user in the browser.
---

`playwright` is already installed in `run_in_python_shell`.

Prefer Python + Playwright for browser tasks. Always use the configured persistent browser settings so login state can be reused.

Use these runtime values when opening or reusing the browser:
- browser binary: `{browser_binary}`
- browser profile: `{browser_profile}`
- CDP URL: `{cdp_url}`
- CDP port: `{cdp_port}`

Preferred pattern:
- if a persistent browser is already running on `{cdp_url}`, connect to it with `connect_over_cdp(...)`
- otherwise launch a new persistent browser with the same binary, profile, and CDP port
- the same profile folder can reuse saved login sessions across browser restarts, but do not launch a second persistent browser against that profile while another one is already open

Example:

```python
from pathlib import Path
from playwright.sync_api import BrowserType, sync_playwright

profile_dir = Path("{browser_profile}")
binary = "{browser_binary}"
cdp_url = "{cdp_url}"
cdp_port = "{cdp_port}"
screenshot = Path("browser-home.png")

with sync_playwright() as playwright:
    try:
        browser = playwright.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
    except Exception:
        browser_type: BrowserType = playwright.chromium
        context = browser_type.launch_persistent_context(
            user_data_dir=str(profile_dir),
            executable_path=binary,
            headless=False,
            args=[f"--remote-debugging-port={cdp_port}"],
        )

    page = context.pages[0] if context.pages else context.new_page()
    page.goto("https://example.com", wait_until="domcontentloaded")
    page.screenshot(path=str(screenshot), full_page=True)
    print(page.title())
    print(screenshot)
```

Use `load_image` tool for seeing saved screenshots.

If the website requires 2FA or user's login, ask them to run `faltoobot browser` on your machine. This will open the browser for the user to complete the login step.

Screenshot guidance:
- save screenshots inside the current `workspace` directory
- prefer screenshots before complex interactions or when inspecting layouts
- also print useful text/DOM state to stdout so the model has both visual and textual context

Useful patterns:
- open JS-heavy websites that need a real browser
- reuse the user's logged-in session for websites that block simple scraping
- inspect pages visually with screenshots while also printing useful page state
- keep the shared browser open unless the user explicitly asks to close it
