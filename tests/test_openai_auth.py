import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

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
        allow_groups=False,
        allow_group_chats=set(),
        allowed_chats=set(),
        bot_name="Faltoo",
        browser_binary="",
    )


async def _oauth_token(api_key: str | Callable[[], Awaitable[str]]) -> str:
    if isinstance(api_key, str):
        return api_key
    return await api_key()


@pytest.fixture
def auth_payload() -> dict[str, object]:
    return {
        "tokens": {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "account_id": "account-123",
        }
    }


@dataclass(frozen=True)
class ClientOptionsCase:
    api_key: str
    use_auth_file: bool
    expected_base_url: str | None
    expected_headers: dict[str, str] | None
    expected_token: str


def test_openai_oauth_client_id_allows_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FALTOOBOT_OPENAI_OAUTH_CLIENT_ID", "app-test")

    assert openai_auth.openai_oauth_client_id() == "app-test"


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            ClientOptionsCase(
                api_key="sk-test-key",
                use_auth_file=False,
                expected_base_url=None,
                expected_headers=None,
                expected_token="sk-test-key",
            ),
            id="prefers-api-key",
        ),
        pytest.param(
            ClientOptionsCase(
                api_key="sk-test-key",
                use_auth_file=True,
                expected_base_url=openai_auth.CHATGPT_OAUTH_BASE_URL,
                expected_headers={
                    openai_auth.CHATGPT_ACCOUNT_HEADER: "account-123",
                    openai_auth.CHATGPT_BETA_HEADER: openai_auth.CHATGPT_BETA_VALUE,
                },
                expected_token="access-token",
            ),
            id="prefers-oauth-over-api-key",
        ),
        pytest.param(
            ClientOptionsCase(
                api_key="",
                use_auth_file=True,
                expected_base_url=openai_auth.CHATGPT_OAUTH_BASE_URL,
                expected_headers={
                    openai_auth.CHATGPT_ACCOUNT_HEADER: "account-123",
                    openai_auth.CHATGPT_BETA_HEADER: openai_auth.CHATGPT_BETA_VALUE,
                },
                expected_token="access-token",
            ),
            id="uses-codex-oauth",
        ),
    ],
)
def test_get_openai_client_options(
    case: ClientOptionsCase,
    auth_payload: dict[str, object],
    tmp_path: Path,
) -> None:
    oauth = ""
    if case.use_auth_file:
        auth_file = tmp_path / ".faltoobot" / "auth.json"
        _write_auth(auth_file, auth_payload)
        oauth = str(auth_file)

    resolved_api_key, base_url, default_headers = openai_auth.get_openai_client_options(
        _config(tmp_path, api_key=case.api_key, oauth=oauth)
    )

    assert base_url == case.expected_base_url
    assert default_headers == case.expected_headers
    assert asyncio.run(_oauth_token(resolved_api_key)) == case.expected_token


@pytest.mark.parametrize(
    ("api_key", "oauth", "expected"),
    [
        pytest.param("", "auth.json", True, id="oauth-configured"),
        pytest.param("sk-test-key", "", False, id="api-key-configured"),
    ],
)
def test_uses_chatgpt_oauth_is_config_based(
    api_key: str,
    oauth: str,
    expected: bool,
    tmp_path: Path,
) -> None:
    assert (
        openai_auth.uses_chatgpt_oauth(_config(tmp_path, api_key=api_key, oauth=oauth))
        is expected
    )


def test_oauth_provider_refreshes_auth_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
