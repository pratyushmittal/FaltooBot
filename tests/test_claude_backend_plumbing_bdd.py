import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from faltoobot import claude_cli, config as config_module, gpt_utils, migrate, sessions
from faltoobot.config import Config
from faltoobot.gpt_utils import MessageHistory

scenarios("features/claude_backend_plumbing.feature")

CHAT_KEY = "15555550123@s.whatsapp.net"
SESSION_ID = "session-1"


@pytest.fixture
def ctx(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / ".faltoobot"
    monkeypatch.setattr(sessions, "app_root", lambda: root)
    monkeypatch.setattr(
        sessions,
        "build_config",
        lambda: cast(
            Config,
            SimpleNamespace(
                root=root,
                openai_model="gpt-5-mini",
                openai_api_key="test-key",
                openai_oauth="",
                openai_thinking="low",
                openai_fast=False,
            ),
        ),
    )
    monkeypatch.setattr(
        sessions, "get_system_instructions", lambda config, chat_key, workspace: "sys"
    )
    return {"root": root, "calls": []}


def _save_session_file(root: Path, payload: dict[str, Any]) -> None:
    path = root / "sessions" / CHAT_KEY / SESSION_ID / sessions.MESSAGES_FILE
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _assistant(text: str) -> dict[str, Any]:
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }


@given("a saved session file without a backend key")
def legacy_session_file(ctx: dict[str, Any]) -> None:
    _save_session_file(ctx["root"], {"messages": [], "message_ids": []})


@given(parsers.parse('a saved session file with backend "{backend}"'))
def session_file_with_backend(ctx: dict[str, Any], backend: str) -> None:
    _save_session_file(
        ctx["root"], {"messages": [], "message_ids": [], "backend": backend}
    )


@given(parsers.parse('config.toml sets the llm backend to "{backend}"'))
def config_sets_backend(ctx: dict[str, Any], backend: str) -> None:
    root: Path = ctx["root"]
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.toml").write_text(f'llm_backend = "{backend}"\n', encoding="utf-8")


@given("the Claude bridge is faked")
def fake_claude_bridge(ctx: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_stream_reply(
        session: Any, messages_json: Any, *, config: Any, **_: Any
    ):
        ctx["calls"].append("claude")
        messages_json["messages"].append(
            gpt_utils._to_message_item(_assistant("from claude"))
        )
        yield SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(output=[], output_text="from claude", usage=None),
        )

    monkeypatch.setattr(claude_cli, "stream_reply", fake_stream_reply)


@given("the OpenAI stream is faked")
def fake_openai_stream(ctx: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_streaming_reply(
        config: Any,
        instructions: str,
        input: MessageHistory,
        tools: list[Any],
        prompt_cache_key: str | None = None,
    ):
        ctx["calls"].append("openai")
        input.append(gpt_utils._to_message_item(_assistant("from openai")))
        yield SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(output=[], output_text="from openai", usage=None),
        )

    monkeypatch.setattr(sessions, "_get_streaming_reply", fake_get_streaming_reply)


@given("a config with an invalid claude effort and a negative idle timeout")
def invalid_claude_config(ctx: dict[str, Any]) -> None:
    ctx["raw"] = {"claude": {"effort": "extreme", "idle_timeout": -5}}


@when("I load the session")
def load_session(ctx: dict[str, Any]) -> None:
    ctx["session"] = sessions.Session(chat_key=CHAT_KEY, session_id=SESSION_ID)
    ctx["messages_json"] = sessions.get_messages(ctx["session"])


@when("I create a new session")
def create_session(ctx: dict[str, Any]) -> None:
    ctx["session"] = sessions.get_session(CHAT_KEY)
    ctx["messages_json"] = sessions.get_messages(ctx["session"])


@when("I ask a question")
def ask_question(ctx: dict[str, Any]) -> None:
    session = sessions.Session(chat_key=CHAT_KEY, session_id=SESSION_ID)

    async def run() -> str:
        await sessions.append_user_turn(session, question="hello")
        return await sessions.get_answer(session)

    ctx["answer"] = asyncio.run(run())


@when("I render the config")
def render_config(ctx: dict[str, Any]) -> None:
    ctx["merged"] = config_module.merge_config(ctx["raw"])
    ctx["rendered"] = config_module.render_config(ctx["raw"])


@then(parsers.parse('the session backend is "{backend}"'))
def backend_is(ctx: dict[str, Any], backend: str) -> None:
    assert sessions.get_backend(ctx["messages_json"]) == backend


@when("the update migrations run")
def run_migrations(ctx: dict[str, Any]) -> None:
    root: Path = ctx["root"]
    path = root / "sessions" / CHAT_KEY / SESSION_ID / sessions.MESSAGES_FILE
    os.utime(path, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
    ctx["mtime"] = path.stat().st_mtime_ns
    ctx["migration_config"] = cast(
        Config, SimpleNamespace(root=root, sessions_dir=root / "sessions")
    )
    ctx["session"] = sessions.Session(chat_key=CHAT_KEY, session_id=SESSION_ID)
    ctx["changed"] = migrate.add_session_backend(ctx["migration_config"])
    assert ctx["changed"] is True


@then("the saved messages.json keeps the backend key")
def saved_keys(ctx: dict[str, Any]) -> None:
    saved = json.loads(ctx["session"].messages_path.read_text(encoding="utf-8"))
    assert saved["backend"] in config_module.BACKEND_OPTIONS
    # comment: no Claude conversation exists yet for these sessions.
    assert not saved.get("claude_session_id")


@then("the session file's modification time is unchanged")
def mtime_unchanged(ctx: dict[str, Any]) -> None:
    assert ctx["session"].messages_path.stat().st_mtime_ns == ctx["mtime"]


@then("running the migrations again changes nothing")
def migrations_idempotent(ctx: dict[str, Any]) -> None:
    assert migrate.add_session_backend(ctx["migration_config"]) is False


@then("the Claude bridge answered")
def claude_answered(ctx: dict[str, Any]) -> None:
    assert ctx["answer"] == "from claude"


@then("the OpenAI stream answered")
def openai_answered(ctx: dict[str, Any]) -> None:
    assert ctx["answer"] == "from openai"


@then("the OpenAI stream was not used")
def openai_unused(ctx: dict[str, Any]) -> None:
    assert ctx["calls"] == ["claude"]


@then("the Claude bridge was not used")
def claude_unused(ctx: dict[str, Any]) -> None:
    assert ctx["calls"] == ["openai"]


@then("the rendered config has the llm_backend key and the claude section")
def rendered_sections(ctx: dict[str, Any]) -> None:
    assert ctx["rendered"].startswith('# Faltoobot config\n\nllm_backend = "openai"\n')
    assert "[claude]" in ctx["rendered"]


@then(
    parsers.parse('the claude effort is "{effort}" and the idle timeout is {timeout:d}')
)
def claude_defaults(ctx: dict[str, Any], effort: str, timeout: int) -> None:
    assert ctx["merged"]["claude"]["effort"] == effort
    assert ctx["merged"]["claude"]["idle_timeout"] == timeout
