import asyncio
import base64
import json
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeAlias
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from faltoobot.config import Config, app_root

AUTH_FILE_NAME = "auth.json"
CHATGPT_OAUTH_BASE_URL = "https://chatgpt.com/backend-api/codex/"
CHATGPT_ACCOUNT_HEADER = "chatgpt-account-id"
CHATGPT_ORIGINATOR_HEADER = "originator"
CHATGPT_ORIGINATOR_VALUE = "codex_cli_rs"
# comment: OpenAI's Codex OAuth flow uses this fixed client id. Keep it here so
# refresh and login match the same first-party OAuth app, while still allowing an
# env override if OpenAI rotates it in the future.
CHATGPT_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CHATGPT_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
REFRESH_MARGIN = timedelta(minutes=5)
REFRESH_INTERVAL = timedelta(minutes=55)
JWT_PART_COUNT = 3

JsonObject: TypeAlias = dict[str, Any]
OpenAIClientOptions: TypeAlias = tuple[
    str | Callable[[], Awaitable[str]],
    str | None,
    dict[str, str] | None,
]


class OpenAIAuthError(RuntimeError):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC)


def faltoobot_auth_file() -> Path:
    return app_root() / AUTH_FILE_NAME


def openai_oauth_client_id() -> str:
    return (
        os.environ.get("FALTOOBOT_OPENAI_OAUTH_CLIENT_ID", "").strip()
        or CHATGPT_OAUTH_CLIENT_ID
    )


def save_chatgpt_oauth_tokens(
    auth_file: Path,
    *,
    id_token: str,
    access_token: str,
    refresh_token: str,
) -> None:
    account_id = _account_id_from_tokens({"id_token": id_token})
    if not account_id:
        raise OpenAIAuthError("ChatGPT login did not return an account id.")
    _write_json(
        auth_file,
        {
            "auth_mode": "chatgpt",
            "tokens": {
                "id_token": id_token,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "account_id": account_id,
            },
            "last_refresh": _utcnow().isoformat().replace("+00:00", "Z"),
        },
    )


def _configured_auth_file(config: Config) -> Path | None:
    oauth = _string(config.openai_oauth)
    if not oauth:
        return None
    return Path(oauth).expanduser()


def _read_json(path: Path) -> JsonObject:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise OpenAIAuthError(f"ChatGPT auth file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise OpenAIAuthError(
            f"ChatGPT auth file is invalid JSON: {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise OpenAIAuthError(f"ChatGPT auth file must contain a JSON object: {path}")
    return value


def _write_json(path: Path, data: JsonObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)


def _token_object(data: JsonObject) -> JsonObject:
    tokens = data.get("tokens")
    return tokens if isinstance(tokens, dict) else {}


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _jwt_claims(token: str) -> JsonObject:
    parts = token.split(".")
    if len(parts) != JWT_PART_COUNT:
        return {}
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding)
        value = json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _account_id_from_tokens(tokens: JsonObject) -> str:
    account_id = _string(tokens.get("account_id"))
    if account_id:
        return account_id
    id_token = _string(tokens.get("id_token"))
    claims = _jwt_claims(id_token)
    auth = claims.get("https://api.openai.com/auth")
    if isinstance(auth, dict):
        return _string(auth.get("chatgpt_account_id"))
    return ""


def _access_token_expiration(access_token: str) -> datetime | None:
    claims = _jwt_claims(access_token)
    exp = claims.get("exp")
    if not isinstance(exp, int):
        return None
    return datetime.fromtimestamp(exp, tz=UTC)


def _parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _needs_refresh(data: JsonObject) -> bool:
    tokens = _token_object(data)
    access_token = _string(tokens.get("access_token"))
    if not access_token:
        return True

    expiration = _access_token_expiration(access_token)
    if expiration and expiration <= _utcnow() + REFRESH_MARGIN:
        return True

    last_refresh = _parse_iso_datetime(_string(data.get("last_refresh")))
    if last_refresh and last_refresh <= _utcnow() - REFRESH_INTERVAL:
        return True
    return False


def _auth_file_error() -> OpenAIAuthError:
    return OpenAIAuthError(
        "OpenAI auth missing. Set openai.oauth, set OPENAI_API_KEY, or run `faltoobot login`."
    )


def uses_chatgpt_oauth(config: Config) -> bool:
    return _configured_auth_file(config) is not None


def _token_url() -> str:
    return (
        os.environ.get(
            "CODEX_REFRESH_TOKEN_URL_OVERRIDE", CHATGPT_OAUTH_TOKEN_URL
        ).strip()
        or CHATGPT_OAUTH_TOKEN_URL
    )


def _request_token_refresh(refresh_token: str) -> JsonObject:
    request = Request(
        _token_url(),
        data=json.dumps(
            {
                "client_id": openai_oauth_client_id(),
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise OpenAIAuthError(
            f"ChatGPT token refresh failed with status {exc.code}: {detail}"
        ) from exc
    except URLError as exc:
        raise OpenAIAuthError(f"ChatGPT token refresh failed: {exc}") from exc

    try:
        value = json.loads(body)
    except json.JSONDecodeError as exc:
        raise OpenAIAuthError(
            f"ChatGPT token refresh returned invalid JSON: {body}"
        ) from exc
    if not isinstance(value, dict):
        raise OpenAIAuthError("ChatGPT token refresh must return a JSON object.")
    return value


def _refresh_access_token(auth_file: Path) -> str:
    data = _read_json(auth_file)
    tokens = _token_object(data)
    refresh_token = _string(tokens.get("refresh_token"))
    if not refresh_token:
        raise OpenAIAuthError(
            f"ChatGPT refresh token is missing from {auth_file}. Run `faltoobot login` again."
        )

    payload = _request_token_refresh(refresh_token)
    access_token = _string(payload.get("access_token"))
    if not access_token:
        raise OpenAIAuthError("ChatGPT token refresh did not return an access_token.")

    id_token = _string(payload.get("id_token")) or _string(tokens.get("id_token"))
    next_refresh_token = _string(payload.get("refresh_token")) or refresh_token
    account_id = _string(tokens.get("account_id")) or _account_id_from_tokens(
        {"id_token": id_token}
    )

    updated = dict(data)
    updated_tokens = dict(tokens)
    updated_tokens["access_token"] = access_token
    updated_tokens["refresh_token"] = next_refresh_token
    if id_token:
        updated_tokens["id_token"] = id_token
    if account_id:
        updated_tokens["account_id"] = account_id
    updated["tokens"] = updated_tokens
    updated["last_refresh"] = _utcnow().isoformat().replace("+00:00", "Z")
    _write_json(auth_file, updated)
    return access_token


def get_openai_client_options(config: Config) -> OpenAIClientOptions:
    auth_file = _configured_auth_file(config)
    if auth_file is None:
        if config.openai_api_key:
            return config.openai_api_key, None, None
        raise _auth_file_error()

    tokens = _token_object(_read_json(auth_file))
    account_id = _account_id_from_tokens(tokens)
    if not account_id:
        raise OpenAIAuthError(
            f"ChatGPT account id is missing from {auth_file}. Run `faltoobot login` again."
        )

    lock = asyncio.Lock()

    async def oauth_api_key() -> str:
        async with lock:
            data = _read_json(auth_file)
            tokens = _token_object(data)
            access_token = _string(tokens.get("access_token"))
            if not _needs_refresh(data):
                return access_token
            # comment: ChatGPT OAuth auth.json can hold an expired short-lived access token,
            # so refresh it on demand before sending requests to the Codex backend.
            return await asyncio.to_thread(_refresh_access_token, auth_file)

    return (
        oauth_api_key,
        # comment: the SDK appends resource paths like `/responses`, so the base URL must end
        # at `/codex/` rather than `/codex/responses`.
        CHATGPT_OAUTH_BASE_URL,
        {
            CHATGPT_ACCOUNT_HEADER: account_id,
            # comment: upstream Codex identifies first-party ChatGPT OAuth traffic with the
            # `originator` header rather than the older `OpenAI-Beta: responses=experimental`.
            CHATGPT_ORIGINATOR_HEADER: CHATGPT_ORIGINATOR_VALUE,
        },
    )
