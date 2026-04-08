from __future__ import annotations

import time
from pathlib import Path

from playwright.sync_api import BrowserType, Page, sync_playwright

CDP_PORT = 9222
PROFILE_DIR_NAME = "faltoobot"


def playwright_chromium_binary() -> str:
    with sync_playwright() as playwright:
        return playwright.chromium.executable_path


def browser_profile_dir(root: Path) -> Path:
    return root / PROFILE_DIR_NAME


def _page(context: object) -> Page:
    pages = list(getattr(context, "pages", []))
    if pages:
        return pages[0]
    return context.new_page()  # type: ignore[no-any-return]


def open_browser(*, root: Path, binary: str, url: str | None = None) -> None:
    profile_dir = browser_profile_dir(root)
    profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser_type: BrowserType = playwright.chromium
        context = browser_type.launch_persistent_context(
            user_data_dir=str(profile_dir),
            executable_path=binary,
            headless=False,
            args=[f"--remote-debugging-port={CDP_PORT}"],
        )
        page = _page(context)
        if url:
            page.goto(url, wait_until="domcontentloaded")

        print(f"Browser launched: {binary}")
        print(f"CDP: http://127.0.0.1:{CDP_PORT}")
        print(f"Profile: {profile_dir}")
        print("Press Ctrl+C to close the browser.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            context.close()
