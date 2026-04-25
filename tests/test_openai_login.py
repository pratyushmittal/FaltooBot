import io
import json
from pathlib import Path
from urllib.parse import parse_qs

import pytest
from rich.console import Console

from faltoobot import openai_login


class FakeResponse:
    def __init__(self, payload: dict[str, str]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, exc_tb: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class FakeServer:
    def shutdown(self) -> None:
        return None

    def server_close(self) -> None:
        return None


class FakeThread:
    def join(self, timeout: int | None = None) -> None:
        return None


def _jwt(payload: dict[str, object]) -> str:
    def encode(value: dict[str, object]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return openai_login._base64url(raw)

    return f"{encode({'alg': 'none', 'typ': 'JWT'})}.{encode(payload)}.sig"


def _install_login_flow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    callback_mode: str,
    id_token: str,
) -> None:
    seen: dict[str, str] = {}
    monkeypatch.setattr(
        openai_login,
        "faltoobot_auth_file",
        lambda: tmp_path / ".faltoobot" / "auth.json",
    )
    monkeypatch.setattr(openai_login.webbrowser, "open", lambda url: True)
    monkeypatch.setattr(
        openai_login,
        "_save_oauth_path",
        lambda auth_file: tmp_path / ".faltoobot" / "config.toml",
    )
    monkeypatch.setattr(
        openai_login,
        "_exchange_code_for_tokens",
        lambda **kwargs: {
            "id_token": id_token,
            "access_token": "access-token",
            "refresh_token": "refresh-token",
        },
    )

    def fake_start(state: openai_login._CallbackState):
        seen["state"] = state.expected_state
        if callback_mode == "server-callback":
            state.code = "code-123"
            state.done.set()
        return FakeServer(), FakeThread(), "http://localhost:1455/auth/callback"

    monkeypatch.setattr(openai_login, "_start_callback_server", fake_start)
    monkeypatch.setattr(
        Console,
        "input",
        lambda self, prompt="": (
            ""
            if callback_mode == "server-callback"
            else f"http://localhost:1455/auth/callback?code=code-123&state={seen['state']}"
        ),
    )


def test_build_authorize_url_contains_expected_params() -> None:
    url = openai_login._build_authorize_url(
        redirect_uri="http://localhost:1455/auth/callback",
        code_challenge="challenge",
        state="state-123",
    )

    assert "response_type=code" in url
    assert "client_id=app_EMoamEEZ73f0CkXaXp7hrann" in url
    assert "scope=openid%20profile%20email%20offline_access" in url
    assert "+" not in url
    assert "code_challenge=challenge" in url
    assert "code_challenge_method=S256" in url
    assert "codex_cli_simplified_flow=true" in url
    assert "originator=codex_cli_rs" in url


def test_exchange_code_for_tokens_posts_form_encoded_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}

    def fake_urlopen(request, timeout: int):
        seen["url"] = request.full_url
        seen["body"] = request.data.decode("utf-8")
        seen["content_type"] = request.headers["Content-type"]
        return FakeResponse(
            {
                "id_token": "id-token",
                "access_token": "access-token",
                "refresh_token": "refresh-token",
            }
        )

    monkeypatch.setattr(openai_login, "urlopen", fake_urlopen)

    tokens = openai_login._exchange_code_for_tokens(
        code="code-123",
        redirect_uri="http://localhost:1455/auth/callback",
        code_verifier="verifier-123",
    )

    assert seen["url"] == openai_login.CHATGPT_OAUTH_TOKEN_URL
    body = parse_qs(seen["body"])
    assert body == {
        "grant_type": ["authorization_code"],
        "code": ["code-123"],
        "redirect_uri": ["http://localhost:1455/auth/callback"],
        "client_id": [openai_login.openai_oauth_client_id()],
        "code_verifier": ["verifier-123"],
    }
    assert seen["content_type"] == "application/x-www-form-urlencoded"
    assert tokens["access_token"] == "access-token"


@pytest.mark.parametrize(
    "callback_mode",
    [
        pytest.param("server-callback", id="uses-callback-server"),
        pytest.param("pasted-callback-url", id="accepts-pasted-callback-url"),
    ],
)
def test_run_openai_login_saves_auth_file(
    callback_mode: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    id_token = _jwt(
        {
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "account-123",
            }
        }
    )
    _install_login_flow(
        monkeypatch,
        tmp_path,
        callback_mode=callback_mode,
        id_token=id_token,
    )
    output = io.StringIO()

    openai_login.run_openai_login(
        Console(file=output, force_terminal=False, color_system=None)
    )

    auth_file = tmp_path / ".faltoobot" / "auth.json"
    payload = json.loads(auth_file.read_text(encoding="utf-8"))
    assert payload["auth_mode"] == "chatgpt"
    assert payload["tokens"]["account_id"] == "account-123"
    assert payload["tokens"]["access_token"] == "access-token"
    assert payload["tokens"]["refresh_token"] == "refresh-token"
    assert "Saved" in output.getvalue()
