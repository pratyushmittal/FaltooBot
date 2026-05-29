import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from faltoobot import sessions
from faltoobot import websockets as websocket_utils

pytestmark = pytest.mark.external

scenarios("features/websocket_e2e.feature")

EXPECTED_RESPONSE_IDS = 2
MIN_CACHE_TOTAL_TOKENS = 2000
T = TypeVar("T")


@dataclass
class WebsocketE2E:
    tmp_path: Path
    real_home: Path
    loop: asyncio.AbstractEventLoop
    auth_kind: str = ""
    home: Path | None = None
    workspace: Path | None = None
    session: sessions.Session | None = None
    latest_answer: str = ""
    latest_usage: dict[str, Any] | None = None
    last_user_index: int = -1
    response_ids: list[str] = field(default_factory=list)
    prewarm_response_id: str | None = None
    latest_error: Exception | None = None
    prewarm_retry_logs: int = 0


@pytest.fixture
def ws_e2e(tmp_path: Path) -> Iterator[WebsocketE2E]:
    websocket_utils.WEBSOCKET_SESSIONS.clear()
    loop = asyncio.new_event_loop()
    ctx = WebsocketE2E(tmp_path=tmp_path, real_home=Path.home(), loop=loop)
    try:
        yield ctx
    finally:
        _run(ctx, _close_websockets())
        loop.close()


def _run(ctx: WebsocketE2E, awaitable: Awaitable[T]) -> T:
    return ctx.loop.run_until_complete(awaitable)


async def _close_websockets() -> None:
    for session in list(websocket_utils.WEBSOCKET_SESSIONS.values()):
        await websocket_utils._close_session(session)  # type: ignore[attr-defined]
    websocket_utils.WEBSOCKET_SESSIONS.clear()


def _config_text(*, api_key: str = "", oauth: Path | None, model: str) -> str:
    return "\n".join(
        [
            "# Faltoobot websocket E2E config",
            "",
            "[openai]",
            f"api_key = {json.dumps(api_key)}",
            f"oauth = {json.dumps(str(oauth) if oauth else '')}",
            f"model = {json.dumps(model)}",
            'thinking = "none"',
            "fast = true",
            "websocket = true",
            "",
            "[bot]",
            "allow_group_chats = []",
            "allowed_chats = []",
            "",
        ]
    )


def _write_config(
    ctx: WebsocketE2E,
    monkeypatch: pytest.MonkeyPatch,
    *,
    api_key: str = "",
    oauth: Path | None,
    model: str,
) -> None:
    ctx.home = ctx.tmp_path / f"home-{ctx.auth_kind}"
    config_path = ctx.home / ".faltoobot" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        _config_text(api_key=api_key, oauth=oauth, model=model), encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(ctx.home))


def _session(ctx: WebsocketE2E) -> sessions.Session:
    # Step order should create the session before websocket actions.
    if ctx.session is None:
        raise AssertionError("session has not been created")
    return ctx.session


def _state(ctx: WebsocketE2E) -> websocket_utils.OpenAIWebsocketSession:
    payload = sessions.get_messages(_session(ctx))
    state = websocket_utils.WEBSOCKET_SESSIONS.get(payload["id"])
    # Prewarm/streaming should cache websocket state by prompt cache key.
    if state is None:
        raise AssertionError("websocket state was not cached")
    return state


def _assistant_text(item: dict[str, Any]) -> str:
    content = item.get("content")
    # Assistant content can be saved as plain text for simple responses.
    if isinstance(content, str):
        return content.strip()
    # Tool/multimodal content is stored as parts; other shapes have no text.
    if not isinstance(content, list):
        return ""
    return "\n".join(
        text
        for part in content
        if isinstance(part, dict)
        for text in [str(part.get("text") or "").strip()]
        if text
    )


def _latest_assistant_message(ctx: WebsocketE2E) -> str:
    payload = sessions.get_messages(_session(ctx))
    messages = [
        item
        for item in payload["messages"]
        if isinstance(item, dict)
        and item.get("type") == "message"
        and item.get("role") == "assistant"
    ]
    # A completed answer should always persist an assistant message.
    if not messages:
        raise AssertionError("no assistant message was persisted")
    return _assistant_text(messages[-1])


def _append_user_question(ctx: WebsocketE2E, question: str) -> None:
    _run(ctx, sessions.append_user_turn(_session(ctx), question=question))
    ctx.last_user_index = len(sessions.get_messages(_session(ctx))["messages"]) - 1


def _usage_after_last_user(ctx: WebsocketE2E) -> dict[str, Any]:
    messages = sessions.get_messages(_session(ctx))["messages"]
    for item in messages[ctx.last_user_index + 1 :]:
        if not isinstance(item, dict) or item.get("role") != "assistant":
            continue
        usage = item.get("usage")
        if isinstance(usage, dict):
            return usage
    raise AssertionError("no assistant usage found after latest user message")


def _cache_tokens(usage: dict[str, Any]) -> int:
    for key in ("input_cache_tokens", "input_cached_tokens", "cached_input_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            return value

    for details_key in ("input_tokens_details", "prompt_tokens_details"):
        details = usage.get(details_key)
        if not isinstance(details, dict):
            continue
        for key in ("cached_tokens", "cache_read_tokens", "input_cache_tokens"):
            value = details.get(key)
            if isinstance(value, int):
                return value
    return 0


def _cache_tokens_in_history(ctx: WebsocketE2E) -> list[int]:
    return [
        _cache_tokens(usage)
        for item in sessions.get_messages(_session(ctx))["messages"]
        if isinstance(item, dict)
        for usage in [item.get("usage")]
        if isinstance(usage, dict)
    ]


def _write_large_crons_skill(workspace: Path) -> None:
    skills_dir = workspace / ".skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        "description: Long crons reference for websocket token cache E2E",
        "---",
        "Reply with CRONS_SKILL_READ after reading this skill.",
    ]
    for index in range(260):
        lines.append(
            f"cron-{index}: run backup sync cleanup report monitor audit rotate "
            "archive verify notify metrics healthcheck database queue worker "
            "scheduler deployment retention snapshot restore billing analytics"
        )
    (skills_dir / "crons.md").write_text("\n".join(lines), encoding="utf-8")


@given(parsers.parse("Config has {auth} and websocket=true"))
def config_auth(
    ws_e2e: WebsocketE2E, monkeypatch: pytest.MonkeyPatch, auth: str
) -> None:
    if auth == "OpenAI API key":
        _configure_api_key_auth(ws_e2e, monkeypatch)
        return
    if auth == "Codex OAuth":
        _configure_codex_oauth_auth(ws_e2e, monkeypatch)
        return
    if auth == "wrong OpenAI API key":
        _configure_wrong_api_key_auth(ws_e2e, monkeypatch)
        return
    raise AssertionError(f"unsupported auth: {auth}")


def _configure_wrong_api_key_auth(
    ws_e2e: WebsocketE2E, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws_e2e.auth_kind = "wrong-api"
    model = os.environ.get("FALTOOCHAT_E2E_API_MODEL", "").strip()
    model = model or os.environ.get("FALTOOCHAT_E2E_MODEL", "").strip()
    _write_config(
        ws_e2e,
        monkeypatch,
        api_key="sk-faltoobot-invalid-websocket-e2e",
        oauth=None,
        model=model or "gpt-5.5",
    )


def _configure_api_key_auth(
    ws_e2e: WebsocketE2E, monkeypatch: pytest.MonkeyPatch
) -> None:
    # API-key scenario uses the runner-provided OpenAI key.
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise RuntimeError("OPENAI_API_KEY must be set for websocket E2E tests.")
    ws_e2e.auth_kind = "api"
    model = os.environ.get("FALTOOCHAT_E2E_API_MODEL", "").strip()
    model = model or os.environ.get("FALTOOCHAT_E2E_MODEL", "").strip()
    _write_config(ws_e2e, monkeypatch, oauth=None, model=model or "gpt-5.5")


def _configure_codex_oauth_auth(
    ws_e2e: WebsocketE2E, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_path = os.environ.get("FALTOOCHAT_E2E_OPENAI_OAUTH", "").strip()
    oauth = (
        Path(auth_path).expanduser()
        if auth_path
        else ws_e2e.real_home / ".faltoobot" / "auth.json"
    )
    # Codex scenario reuses the runner's logged-in auth file.
    if not oauth.exists():
        raise RuntimeError(
            "Codex auth file must exist for websocket E2E tests. "
            "Run `faltoobot codex-login` or set FALTOOCHAT_E2E_OPENAI_OAUTH."
        )
    ws_e2e.auth_kind = "codex"
    model = os.environ.get("FALTOOCHAT_E2E_CODEX_MODEL", "").strip()
    model = model or os.environ.get("FALTOOCHAT_E2E_MODEL", "").strip()
    _write_config(ws_e2e, monkeypatch, oauth=oauth, model=model or "gpt-5.5")


@given("workspace has a large crons skill")
def workspace_has_large_crons_skill(ws_e2e: WebsocketE2E) -> None:
    if ws_e2e.workspace is None:
        ws_e2e.workspace = ws_e2e.tmp_path / f"workspace-{ws_e2e.auth_kind}"
        ws_e2e.workspace.mkdir()
    _write_large_crons_skill(ws_e2e.workspace)


def _create_session(ws_e2e: WebsocketE2E, monkeypatch: pytest.MonkeyPatch) -> None:
    # Auth setup creates the temp HOME and config before session creation.
    if ws_e2e.home is None:
        raise AssertionError("auth must be configured before creating a session")
    monkeypatch.setenv("HOME", str(ws_e2e.home))
    if ws_e2e.workspace is None:
        ws_e2e.workspace = ws_e2e.tmp_path / f"workspace-{ws_e2e.auth_kind}"
        ws_e2e.workspace.mkdir()
    ws_e2e.session = sessions.get_session(
        chat_key=sessions.get_dir_chat_key(ws_e2e.workspace),
        workspace=ws_e2e.workspace,
    )


@when("I start a Faltoochat session")
def start_faltoochat_session(
    ws_e2e: WebsocketE2E,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="faltoobot")
    _create_session(ws_e2e, monkeypatch)
    try:
        _run(ws_e2e, sessions.prewarm_openai_websocket(_session(ws_e2e)))
    except Exception as exc:
        if ws_e2e.auth_kind != "wrong-api":
            raise
        ws_e2e.latest_error = exc
        ws_e2e.prewarm_retry_logs = sum(
            record.name == "faltoobot"
            and record.message.startswith("Retrying OpenAI websocket prewarm")
            for record in caplog.records
        )
        return

    if ws_e2e.auth_kind == "wrong-api":
        raise AssertionError("websocket prewarm should fail")
    ws_e2e.prewarm_response_id = _state(ws_e2e).previous_response_id


@when(parsers.parse('I ask the assistant to reply with "{token}"'))
def ask_for_token(ws_e2e: WebsocketE2E, token: str) -> None:
    _append_user_question(
        ws_e2e, f"Reply with exactly this token and no extra words: {token}"
    )


@when("I stream the answer")
def stream_answer(ws_e2e: WebsocketE2E) -> None:
    ws_e2e.latest_answer = _run(ws_e2e, sessions.get_answer(_session(ws_e2e)))
    ws_e2e.latest_usage = _usage_after_last_user(ws_e2e)
    response_id = _state(ws_e2e).previous_response_id
    # Completed websocket responses should update the cached response id.
    if response_id is not None:
        ws_e2e.response_ids.append(response_id)


@then("websocket session gets warmed up")
def websocket_session_gets_warmed_up(ws_e2e: WebsocketE2E) -> None:
    assert _state(ws_e2e).ws is not None


@then("websocket session has a previous_response_id")
def websocket_session_has_previous_response_id(ws_e2e: WebsocketE2E) -> None:
    assert ws_e2e.prewarm_response_id
    assert _state(ws_e2e).previous_response_id == ws_e2e.prewarm_response_id


@then("websocket auth error is raised")
def websocket_auth_error_is_raised(ws_e2e: WebsocketE2E) -> None:
    if ws_e2e.latest_error is None:
        raise AssertionError("websocket prewarm did not fail")
    error = f"{type(ws_e2e.latest_error).__name__}: {ws_e2e.latest_error}".lower()
    assert any(
        text in error
        for text in ("401", "unauthorized", "invalid", "api key", "authentication")
    )


@then("websocket prewarm is not retried")
def websocket_prewarm_is_not_retried(ws_e2e: WebsocketE2E) -> None:
    assert ws_e2e.prewarm_retry_logs == 0


@then(parsers.parse('the latest assistant answer contains "{token}"'))
def latest_answer_contains(ws_e2e: WebsocketE2E, token: str) -> None:
    answer = ws_e2e.latest_answer or _latest_assistant_message(ws_e2e)
    assert token in answer
    assert token in _latest_assistant_message(ws_e2e)


@then("the websocket session kept response state across turns")
def websocket_kept_state(ws_e2e: WebsocketE2E) -> None:
    state = _state(ws_e2e)
    assert state.ws is not None
    assert state.previous_response_id
    assert len(ws_e2e.response_ids) >= EXPECTED_RESPONSE_IDS
    assert ws_e2e.response_ids[0] != ws_e2e.prewarm_response_id
    assert ws_e2e.response_ids[-1] != ws_e2e.response_ids[0]
    assert state.input_index > 0


@when("I ask the assistant to read the crons skill")
def ask_to_read_crons_skill(ws_e2e: WebsocketE2E) -> None:
    _append_user_question(
        ws_e2e,
        "Use the local skill named crons, read all of it, then reply with "
        "exactly CRONS_SKILL_READ and no extra words.",
    )


@when("I say thanks")
def say_thanks(ws_e2e: WebsocketE2E) -> None:
    _append_user_question(ws_e2e, "thanks")


@when(parsers.parse('I say "{message}"'))
def say_message(ws_e2e: WebsocketE2E, message: str) -> None:
    _append_user_question(ws_e2e, message)


@then("the latest usage has total tokens more than 2000")
def latest_usage_total_tokens_large(ws_e2e: WebsocketE2E) -> None:
    usage = ws_e2e.latest_usage or {}
    total_tokens = usage.get("total_tokens")
    assert isinstance(total_tokens, int)
    assert total_tokens > MIN_CACHE_TOTAL_TOKENS


@then("the latest assistant answer is not empty")
def latest_assistant_answer_not_empty(ws_e2e: WebsocketE2E) -> None:
    assert (ws_e2e.latest_answer or _latest_assistant_message(ws_e2e)).strip()


@then("the input cache tokens should have never fallen in the full messages history")
def input_cache_tokens_never_fall_in_history(ws_e2e: WebsocketE2E) -> None:
    cache_tokens = _cache_tokens_in_history(ws_e2e)
    assert cache_tokens
    assert cache_tokens == sorted(cache_tokens)
    assert cache_tokens[-1] > cache_tokens[0]


@when("I restart the current Faltoochat session")
def restart_current_faltoochat_session(ws_e2e: WebsocketE2E) -> None:
    session = _session(ws_e2e)
    workspace = ws_e2e.workspace
    if workspace is None:
        raise AssertionError("workspace must exist before restarting the session")

    _run(ws_e2e, _close_websockets())
    ws_e2e.session = sessions.get_session(
        chat_key=session.chat_key,
        session_id=session.session_id,
        workspace=workspace,
    )
    ws_e2e.latest_answer = ""
    ws_e2e.latest_usage = None
    ws_e2e.last_user_index = -1
    _run(ws_e2e, sessions.prewarm_openai_websocket(_session(ws_e2e)))
    ws_e2e.prewarm_response_id = _state(ws_e2e).previous_response_id
