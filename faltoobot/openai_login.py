import base64
import hashlib
import json
import os
from pathlib import Path
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event, Thread
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen

from rich.console import Console

from faltoobot.config import ensure_config_file, load_toml, merge_config, render_config
from faltoobot.openai_auth import (
    CHATGPT_OAUTH_TOKEN_URL,
    OpenAIAuthError,
    faltoobot_auth_file,
    openai_oauth_client_id,
    save_chatgpt_oauth_tokens,
)

DEFAULT_ISSUER = "https://auth.openai.com"
DEFAULT_ORIGINATOR = "codex_cli_rs"
DEFAULT_SCOPE = (
    "openid profile email offline_access api.connectors.read api.connectors.invoke"
)
CALLBACK_PATH = "/auth/callback"
CALLBACK_PORT = 1455
LOGIN_TIMEOUT_SECONDS = 300
SUCCESS_HTML = (
    "<html><body><h1>OpenAI login complete</h1>"
    "<p>You can close this window and return to Faltoobot.</p></body></html>"
)
ERROR_HTML = (
    "<html><body><h1>OpenAI login failed</h1>"
    "<p>You can close this window and return to Faltoobot.</p></body></html>"
)


class _OpenAILoginError(RuntimeError):
    pass


@dataclass(slots=True)
class _CallbackState:
    expected_state: str
    code: str = ""
    error: str = ""
    done: Event = field(default_factory=Event)


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _generate_pkce() -> tuple[str, str]:
    code_verifier = _base64url(os.urandom(64))
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    code_challenge = _base64url(digest)
    return code_verifier, code_challenge


def _generate_state() -> str:
    return _base64url(os.urandom(32))


def _build_authorize_url(
    *,
    redirect_uri: str,
    code_challenge: str,
    state: str,
    issuer: str = DEFAULT_ISSUER,
    client_id: str | None = None,
) -> str:
    client_id = client_id or openai_oauth_client_id()
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": DEFAULT_SCOPE,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "state": state,
            "originator": DEFAULT_ORIGINATOR,
        }
    )
    return f"{issuer}/oauth/authorize?{query}"


def _exchange_code_for_tokens(
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    token_url: str = CHATGPT_OAUTH_TOKEN_URL,
    client_id: str | None = None,
) -> dict[str, str]:
    client_id = client_id or openai_oauth_client_id()
    request = Request(
        token_url,
        data=urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": code_verifier,
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise _OpenAILoginError(
            f"OpenAI token exchange failed with status {exc.code}: {detail}"
        ) from exc
    except URLError as exc:
        raise _OpenAILoginError(f"OpenAI token exchange failed: {exc}") from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise _OpenAILoginError(
            f"OpenAI token exchange returned invalid JSON: {body}"
        ) from exc
    if not isinstance(payload, dict):
        raise _OpenAILoginError("OpenAI token exchange must return a JSON object.")

    id_token = payload.get("id_token")
    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    if not all(
        isinstance(value, str) and value
        for value in (id_token, access_token, refresh_token)
    ):
        raise _OpenAILoginError(
            "OpenAI token exchange response is missing required tokens."
        )

    return {
        "id_token": id_token,
        "access_token": access_token,
        "refresh_token": refresh_token,
    }


def _send_html(handler: BaseHTTPRequestHandler, *, status: int, body: str) -> None:
    encoded = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.end_headers()
    handler.wfile.write(encoded)


def _handler(state: _CallbackState) -> type[BaseHTTPRequestHandler]:
    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != CALLBACK_PATH:
                _send_html(self, status=404, body=ERROR_HTML)
                return

            params = parse_qs(parsed.query)
            error = next(iter(params.get("error", [])), "")
            error_description = next(iter(params.get("error_description", [])), "")
            if error:
                # comment: the OAuth provider can redirect with an explicit error instead of a code.
                state.error = error_description or error
                state.done.set()
                _send_html(self, status=400, body=ERROR_HTML)
                return

            callback_state = next(iter(params.get("state", [])), "")
            if callback_state != state.expected_state:
                # comment: reject mismatched state values to avoid accepting a stale or foreign callback.
                state.error = "State mismatch."
                state.done.set()
                _send_html(self, status=400, body=ERROR_HTML)
                return

            code = next(iter(params.get("code", [])), "")
            if not code:
                # comment: the callback can arrive without a code when the browser flow is interrupted.
                state.error = "Missing authorization code."
                state.done.set()
                _send_html(self, status=400, body=ERROR_HTML)
                return

            state.code = code
            state.done.set()
            _send_html(self, status=200, body=SUCCESS_HTML)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return CallbackHandler


def _save_oauth_path(auth_file: Path) -> Path:
    config_file = ensure_config_file()
    data = merge_config(load_toml(config_file))
    data["openai"]["oauth"] = str(auth_file)
    rendered = render_config(data)
    if config_file.exists() and config_file.read_text(encoding="utf-8") == rendered:
        return config_file
    config_file.write_text(rendered, encoding="utf-8")
    return config_file


def _start_callback_server(state: _CallbackState) -> tuple[HTTPServer, Thread, str]:
    try:
        server = HTTPServer(("127.0.0.1", CALLBACK_PORT), _handler(state))
    except OSError as exc:
        raise _OpenAILoginError(
            f"Couldn't bind localhost:{CALLBACK_PORT}. Close any previous faltoobot login window and try again."
        ) from exc
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    redirect_uri = f"http://localhost:{CALLBACK_PORT}{CALLBACK_PATH}"
    return server, thread, redirect_uri


def run_openai_login(console: Console | None = None) -> None:
    console = console or Console()
    code_verifier, code_challenge = _generate_pkce()
    callback_state = _CallbackState(expected_state=_generate_state())
    server, thread, redirect_uri = _start_callback_server(callback_state)
    try:
        authorize_url = _build_authorize_url(
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            state=callback_state.expected_state,
        )
        console.print("[bold]OpenAI Codex login[/]")
        console.print("Open this URL if your browser does not open automatically:")
        console.print(authorize_url)
        try:
            webbrowser.open(authorize_url)
        except webbrowser.Error:
            pass

        console.print("Waiting for the browser callback...")
        if not callback_state.done.wait(LOGIN_TIMEOUT_SECONDS):
            raise SystemExit("OpenAI login timed out. Please try again.")
        if callback_state.error:
            raise SystemExit(f"OpenAI login failed: {callback_state.error}")
        if not callback_state.code:
            raise SystemExit("OpenAI login failed: missing authorization code.")

        tokens = _exchange_code_for_tokens(
            code=callback_state.code,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
        )
        auth_file = faltoobot_auth_file()
        save_chatgpt_oauth_tokens(
            auth_file,
            id_token=tokens["id_token"],
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
        )
        config_file = _save_oauth_path(auth_file)
        console.print(f"[green]Saved[/] [cyan]{auth_file}[/]")
        console.print(f"[green]Updated[/] [cyan]{config_file}[/] with openai.oauth")
    except OpenAIAuthError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
