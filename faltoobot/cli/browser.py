from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

CDP_PORT = 9222
CDP_CONNECT_TIMEOUT_MS = 15_000
PROFILE_DIR_NAME = "faltoobot"


def cdp_url() -> str:
    return f"http://127.0.0.1:{CDP_PORT}"


def _cdp_version() -> dict[str, object] | None:
    try:
        with urlopen(f"{cdp_url()}/json/version", timeout=1) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError):
        return None


def _cdp_is_running() -> bool:
    return _cdp_version() is not None


def _running_cdp_commands() -> list[str]:
    try:
        result = subprocess.run(
            ["ps", "-axo", "command="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return []
    marker = f"--remote-debugging-port={CDP_PORT}"
    return [line for line in result.stdout.splitlines() if marker in line]


def _command_uses_profile(command: str, profile_dir: Path) -> bool:
    expected = str(profile_dir.expanduser().resolve())
    prefix = "--user-data-dir="
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    for index, part in enumerate(parts):
        if part == "--user-data-dir" and index + 1 < len(parts):
            raw = parts[index + 1]
        elif part.startswith(prefix):
            raw = part[len(prefix) :]
        else:
            continue
        raw = raw.strip("\"'")
        try:
            actual = str(Path(raw).expanduser().resolve())
        except OSError:
            actual = raw
        return actual == expected
    return False


def _cdp_profile_matches(profile_dir: Path) -> bool:
    """Return whether the running CDP browser uses FaltooBot's profile."""
    commands = _running_cdp_commands()
    if not commands:
        # If CDP answers but we cannot inspect the process table, avoid claiming a
        # reusable FaltooBot profile. Launching against the same port/profile can
        # otherwise make users log in to the wrong browser profile.
        return False
    return any(_command_uses_profile(command, profile_dir) for command in commands)


def _open_url_in_existing_cdp(url: str) -> None:
    encoded = quote(url, safe="")
    request = Request(f"{cdp_url()}/json/new?{encoded}", method="PUT")
    try:
        with urlopen(request, timeout=2):
            return
    except OSError:
        # Opening a new tab is a convenience only; the persistent browser is still
        # reusable even if this endpoint is unavailable on a Chromium build.
        return


def connect_existing_browser_context(
    playwright, *, root: Path, timeout_ms: int = CDP_CONNECT_TIMEOUT_MS
):
    """Connect to FaltooBot's shared CDP browser and return its first context.

    Background jobs use this instead of calling Playwright's default
    connect_over_cdp directly because the default timeout can be several
    minutes when Chrome's CDP socket is half-alive. A bounded timeout lets
    cron jobs fail fast and surface a clear health-check error.
    """
    profile_dir = browser_profile_dir(root)
    if not _cdp_is_running():
        raise RuntimeError(
            f"FaltooBot browser is not running on {cdp_url()}. Run `faltoobot browser`."
        )
    if not _cdp_profile_matches(profile_dir):
        commands = "\n".join(_running_cdp_commands()) or "(unable to inspect process)"
        raise RuntimeError(
            "A browser is listening on FaltooBot's CDP port, but it does not "
            "appear to be using the FaltooBot profile. "
            f"Expected profile: {profile_dir}\nDetected CDP process(es):\n{commands}"
        )

    try:
        browser = playwright.chromium.connect_over_cdp(cdp_url(), timeout=timeout_ms)
    except TypeError:
        # Backward compatibility for older Playwright versions.
        browser = playwright.chromium.connect_over_cdp(cdp_url())
    if not browser.contexts:
        raise RuntimeError("Connected browser has no reusable login context")
    return browser, browser.contexts[0]


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
        if not _cdp_profile_matches(profile_dir):
            commands = (
                "\n".join(_running_cdp_commands()) or "(unable to inspect process)"
            )
            raise SystemExit(
                "A browser is already listening on FaltooBot's CDP port, but it "
                "does not appear to be using the FaltooBot profile. Close that "
                "browser before running `faltoobot browser` so logins are saved "
                f"in the correct profile.\nExpected profile: {profile_dir}\n"
                f"Detected CDP process(es):\n{commands}"
            )
        if url:
            _open_url_in_existing_cdp(url)
        print("Browser already running.")
        print(f"CDP: {cdp_url()}")
        print(f"Profile: {profile_dir}")
        if url:
            print(f"Opened URL: {url}")
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
