import asyncio
import json
import ssl
from collections.abc import Awaitable, Callable
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from faltoobot import openai_auth
from faltoobot.config import Config


def _write_auth(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _config(tmp_path: Path, *, api_key: str, oauth: str) -> Config:
    root = tmp_path / ".faltoobot"
    return Config(
        home=tmp_path,
        root=root,
        config_file=root / "config.toml",
        log_file=root / "faltoobot.log",
        sessions_dir=root / "sessions",
        session_db=root / "sessions.sqlite3",
        launch_agent=root / "launch-agent.sh",
        run_script=root / "run.sh",
        openai_api_key=api_key,
        openai_oauth=oauth,
        openai_model="gpt-5-mini",
        openai_thinking="low",
        openai_fast=False,
        openai_transcription_model="gpt-4o-transcribe",
        allow_group_chats=set(),
        allowed_chats=set(),
        bot_name="Faltoo",
        browser_binary="",
    )


async def _oauth_token(api_key: str | Callable[[], Awaitable[str]]) -> str:
    if isinstance(api_key, str):
        raise AssertionError("expected oauth api key provider")
    return await api_key()


def test_openai_oauth_client_id_allows_env_override(monkeypatch) -> None:
    monkeypatch.setenv("FALTOOBOT_OPENAI_OAUTH_CLIENT_ID", "app-test")

    assert openai_auth.openai_oauth_client_id() == "app-test"


def test_get_openai_client_options_prefers_api_key(tmp_path: Path) -> None:
    api_key, base_url, default_headers = openai_auth.get_openai_client_options(
        _config(tmp_path, api_key="sk-test-key", oauth="")
    )

    assert api_key == "sk-test-key"
    assert base_url is None
    assert default_headers is None


def test_get_openai_client_options_prefers_oauth_over_api_key(
    tmp_path: Path,
) -> None:
    auth_file = tmp_path / ".faltoobot" / "auth.json"
    _write_auth(
        auth_file,
        {
            "tokens": {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "account_id": "account-123",
            }
        },
    )
    api_key, base_url, default_headers = openai_auth.get_openai_client_options(
        _config(tmp_path, api_key="sk-test-key", oauth=str(auth_file))
    )

    assert base_url == openai_auth.CHATGPT_OAUTH_BASE_URL
    assert asyncio.run(_oauth_token(api_key)) == "access-token"
    assert default_headers == {
        openai_auth.CHATGPT_ACCOUNT_HEADER: "account-123",
        openai_auth.CHATGPT_ORIGINATOR_HEADER: openai_auth.CHATGPT_ORIGINATOR_VALUE,
    }


def test_get_openai_client_options_uses_codex_oauth(tmp_path: Path) -> None:
    auth_file = tmp_path / ".faltoobot" / "auth.json"
    _write_auth(
        auth_file,
        {
            "tokens": {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "account_id": "account-123",
            }
        },
    )
    api_key, base_url, default_headers = openai_auth.get_openai_client_options(
        _config(tmp_path, api_key="", oauth=str(auth_file))
    )

    assert base_url == openai_auth.CHATGPT_OAUTH_BASE_URL
    assert default_headers == {
        openai_auth.CHATGPT_ACCOUNT_HEADER: "account-123",
        openai_auth.CHATGPT_ORIGINATOR_HEADER: openai_auth.CHATGPT_ORIGINATOR_VALUE,
    }
    assert asyncio.run(_oauth_token(api_key)) == "access-token"


def test_uses_chatgpt_oauth_is_config_based(tmp_path: Path) -> None:
    assert openai_auth.uses_chatgpt_oauth(
        _config(tmp_path, api_key="", oauth="auth.json")
    )
    assert not openai_auth.uses_chatgpt_oauth(
        _config(tmp_path, api_key="sk-test-key", oauth="")
    )


def test_oauth_provider_refreshes_auth_json(monkeypatch, tmp_path: Path) -> None:
    auth_file = tmp_path / ".faltoobot" / "auth.json"
    _write_auth(
        auth_file,
        {
            "tokens": {
                "refresh_token": "refresh-token",
                "account_id": "account-123",
            }
        },
    )
    monkeypatch.setattr(
        openai_auth,
        "_request_token_refresh",
        lambda refresh_token: {
            "access_token": f"new-{refresh_token}",
            "refresh_token": "new-refresh-token",
        },
    )

    api_key, _, _ = openai_auth.get_openai_client_options(
        _config(tmp_path, api_key="", oauth=str(auth_file))
    )

    assert asyncio.run(_oauth_token(api_key)) == "new-refresh-token"
    payload = json.loads(auth_file.read_text(encoding="utf-8"))
    assert payload["tokens"]["access_token"] == "new-refresh-token"
    assert payload["tokens"]["refresh_token"] == "new-refresh-token"
    assert payload["last_refresh"]


class FakeResponse:
    pass


def test_open_url_retries_certifi_on_missing_local_ca(monkeypatch) -> None:
    calls: list[object] = []

    def fake_urlopen(request, *, timeout: int, context=None):
        calls.append(context)
        if context is None:
            raise URLError(ssl.SSLCertVerificationError("missing ca"))
        return FakeResponse()

    monkeypatch.setattr(openai_auth, "urlopen", fake_urlopen)

    response = openai_auth.open_url(Request("https://example.test"), timeout=30)

    assert isinstance(response, FakeResponse)
    assert calls[0] is None
    assert calls[1] is not None


def test_open_url_keeps_non_ssl_errors(monkeypatch) -> None:
    def fake_urlopen(request, *, timeout: int, context=None):
        raise URLError("offline")

    monkeypatch.setattr(openai_auth, "urlopen", fake_urlopen)

    with pytest.raises(URLError, match="offline"):
        openai_auth.open_url(Request("https://example.test"), timeout=30)


def test_open_url_keeps_http_errors(monkeypatch) -> None:
    error = HTTPError("https://example.test", 400, "Bad Request", Message(), None)

    def fake_urlopen(request, *, timeout: int, context=None):
        raise error

    monkeypatch.setattr(openai_auth, "urlopen", fake_urlopen)

    with pytest.raises(HTTPError) as exc_info:
        openai_auth.open_url(Request("https://example.test"), timeout=30)
    assert exc_info.value is error
