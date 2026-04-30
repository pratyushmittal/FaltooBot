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
    monkeypatch.setattr(browser, "_cdp_is_running", lambda: False)

    browser.open_browser(root=tmp_path, binary="/tmp/chrome", url=None)

    assert browser.browser_profile_dir(tmp_path).is_dir()
    assert calls == [("wait", None), ("terminate", None), ("wait", 5)]


def test_default_browser_binary_prefers_google_chrome_on_macos(monkeypatch) -> None:
    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

    monkeypatch.setattr(browser.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "exists", lambda self: self == chrome)

    assert browser.default_browser_binary() == str(chrome)


def test_open_browser_reuses_existing_cdp_browser(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    calls: list[object] = []
    opened: list[str] = []

    monkeypatch.setattr(browser, "_cdp_is_running", lambda: True)
    monkeypatch.setattr(browser, "_cdp_profile_matches", lambda profile: True)
    monkeypatch.setattr(
        browser, "_open_url_in_existing_cdp", lambda url: opened.append(url)
    )
    monkeypatch.setattr(
        browser.subprocess,
        "Popen",
        lambda args: calls.append(args),
    )

    browser.open_browser(root=tmp_path, binary="/tmp/chrome", url="https://example.com")

    assert browser.browser_profile_dir(tmp_path).is_dir()
    assert calls == []
    assert opened == ["https://example.com"]
    output = capsys.readouterr().out
    assert "Browser already running." in output
    assert "Opened URL: https://example.com" in output


def test_open_browser_rejects_cdp_for_wrong_profile(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(browser, "_cdp_is_running", lambda: True)
    monkeypatch.setattr(browser, "_cdp_profile_matches", lambda profile: False)
    monkeypatch.setattr(
        browser,
        "_running_cdp_commands",
        lambda: ["/tmp/chrome --remote-debugging-port=9222 --user-data-dir=/tmp/other"],
    )

    try:
        browser.open_browser(root=tmp_path, binary="/tmp/chrome", url=None)
    except SystemExit as exc:
        message = str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected SystemExit")

    assert "does not appear to be using the FaltooBot profile" in message
    assert str(browser.browser_profile_dir(tmp_path)) in message


def test_command_uses_profile_matches_resolved_path(tmp_path: Path) -> None:
    profile = browser.browser_profile_dir(tmp_path)
    profile.mkdir(parents=True)
    command = f"/tmp/chrome --remote-debugging-port=9222 --user-data-dir={profile}"
    assert browser._command_uses_profile(command, profile)


def test_command_uses_profile_matches_quoted_path_with_spaces(tmp_path: Path) -> None:
    profile = browser.browser_profile_dir(tmp_path / "root with spaces")
    profile.mkdir(parents=True)
    command = f'/tmp/chrome --remote-debugging-port=9222 --user-data-dir="{profile}"'
    assert browser._command_uses_profile(command, profile)


def test_command_uses_profile_matches_separate_profile_arg(tmp_path: Path) -> None:
    profile = browser.browser_profile_dir(tmp_path)
    profile.mkdir(parents=True)
    command = f"/tmp/chrome --remote-debugging-port=9222 --user-data-dir {profile}"
    assert browser._command_uses_profile(command, profile)


def test_open_url_in_existing_cdp_encodes_full_url(monkeypatch) -> None:
    seen: list[str] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

    def fake_urlopen(request, timeout: int):
        seen.append(request.full_url)
        return Response()

    monkeypatch.setattr(browser, "urlopen", fake_urlopen)

    browser._open_url_in_existing_cdp("https://example.com/a b?x=1#part")

    assert seen == [
        f"{browser.cdp_url()}/json/new?https%3A%2F%2Fexample.com%2Fa%20b%3Fx%3D1%23part"
    ]
