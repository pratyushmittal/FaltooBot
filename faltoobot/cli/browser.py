from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

CDP_PORT = 9222
PROFILE_DIR_NAME = "faltoobot"


def cdp_url() -> str:
    return f"http://127.0.0.1:{CDP_PORT}"


def _cdp_is_running() -> bool:
    try:
        with urlopen(f"{cdp_url()}/json/version", timeout=1):
            return True
    except URLError:
        return False


def playwright_chromium_binary() -> str:
    with sync_playwright() as playwright:
        return playwright.chromium.executable_path


def browser_profile_dir(root: Path) -> Path:
    return root / PROFILE_DIR_NAME


def default_browser_binary() -> str | None:
    if sys.platform == "darwin":
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if chrome.exists():
            return str(chrome)
        return None
    for name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    ):
        if binary := shutil.which(name):
            return binary
    return None


def _browser_command(binary: str, profile_dir: Path, url: str | None) -> list[str]:
    command = [
        binary,
        f"--user-data-dir={profile_dir}",
        f"--remote-debugging-port={CDP_PORT}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if url:
        command.append(url)
    return command


def open_browser(*, root: Path, binary: str, url: str | None = None) -> None:
    profile_dir = browser_profile_dir(root)
    profile_dir.mkdir(parents=True, exist_ok=True)
    if _cdp_is_running():
        print("Browser already running.")
        print(f"CDP: {cdp_url()}")
        print(f"Profile: {profile_dir}")
        return

    process = subprocess.Popen(_browser_command(binary, profile_dir, url))

    print(f"Browser launched: {binary}")
    print(f"CDP: {cdp_url()}")
    print(f"Profile: {profile_dir}")
    print("Press Ctrl+C to close the browser.")
    try:
        process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
