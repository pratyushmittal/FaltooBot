#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print(
        "Missing dependency: playwright. Install with: python3 -m pip install -r requirements.txt",
        file=sys.stderr,
    )
    sys.exit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Attach Playwright to an existing CDP browser",
    )
    parser.add_argument(
        "--endpoint", default="http://127.0.0.1:9222", help="CDP endpoint URL"
    )
    parser.add_argument("--url", help="Optional URL to open after connecting")
    parser.add_argument("--screenshot", help="Optional screenshot output path")
    parser.add_argument("--timeout-ms", type=int, default=15000)
    parser.add_argument(
        "--close-browser",
        action="store_true",
        help="Close the attached browser at the end. Avoid this for visible live sessions.",
    )
    return parser.parse_args()


def ensure_parent(path_str: str) -> Path:
    path = Path(path_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def main() -> None:
    args = parse_args()

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(args.endpoint)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(args.timeout_ms)

        if args.url:
            page.goto(args.url, wait_until="domcontentloaded")

        print(f"Title: {page.title()}")
        print(f"URL: {page.url}")

        if args.screenshot:
            out = ensure_parent(args.screenshot)
            page.screenshot(path=str(out), full_page=True)
            print(f"Screenshot saved to {out}")

        if args.close_browser:
            browser.close()
            print("Browser closed")
        else:
            print("Detached without closing the browser")


if __name__ == "__main__":
    main()
