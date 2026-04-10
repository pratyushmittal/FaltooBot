from pathlib import Path

from faltoobot.cli import browser


def test_browser_command_uses_profile_and_cdp_url(tmp_path: Path) -> None:
    profile_dir = browser.browser_profile_dir(tmp_path)

    command = browser._browser_command(
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        profile_dir,
        "https://example.com",
    )

    assert command == [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        f"--user-data-dir={profile_dir}",
        f"--remote-debugging-port={browser.CDP_PORT}",
        "--no-first-run",
        "--no-default-browser-check",
        "https://example.com",
    ]


def test_open_browser_terminates_on_keyboard_interrupt(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[tuple[str, int | None]] = []

    class FakeProcess:
        def wait(self, timeout: int | None = None) -> None:
            calls.append(("wait", timeout))
            if timeout is None:
                raise KeyboardInterrupt

        def terminate(self) -> None:
            calls.append(("terminate", None))

        def kill(self) -> None:
            calls.append(("kill", None))

    monkeypatch.setattr(browser.subprocess, "Popen", lambda args: FakeProcess())

    browser.open_browser(root=tmp_path, binary="/tmp/chrome", url=None)

    assert browser.browser_profile_dir(tmp_path).is_dir()
    assert calls == [("wait", None), ("terminate", None), ("wait", 5)]


def test_default_browser_binary_prefers_google_chrome_on_macos(monkeypatch) -> None:
    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

    monkeypatch.setattr(browser.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "exists", lambda self: self == chrome)

    assert browser.default_browser_binary() == str(chrome)
