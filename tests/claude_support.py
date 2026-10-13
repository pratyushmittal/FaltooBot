"""Shared setup for tests that drive the Claude bridge."""

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from faltoobot import claude_cli, sessions, skills
from faltoobot.config import Config

FAKE_CLAUDE = Path(__file__).resolve().parent / "fakes" / "claude"
CHAT_KEY = "15555550123@s.whatsapp.net"
INSTRUCTIONS = "You are Faltoo on WhatsApp. Background update rules apply."


def patch_claude(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """Point sessions, skills and the bridge at temp dirs and the fake `claude`."""
    root = tmp_path / ".faltoobot"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude-log.jsonl"))
    monkeypatch.setattr(sessions, "app_root", lambda: root)
    config = SimpleNamespace(
        root=root,
        openai_model="gpt-5-mini",
        openai_api_key="test-key",
        openai_oauth="",
        openai_thinking="low",
        openai_fast=False,
        openai_websocket=False,
        openrouter_api_key="",
        hook_enabled=False,
        browser_binary="",
        claude_binary=str(FAKE_CLAUDE),
        claude_model="opus",
        claude_effort="medium",
        claude_permission_mode="bypassPermissions",
        claude_idle_timeout=600,
    )
    for module in (sessions, skills):
        monkeypatch.setattr(module, "build_config", lambda: cast(Config, config))
    for module in (sessions, claude_cli):
        monkeypatch.setattr(module, "get_system_instructions", lambda *_: INSTRUCTIONS)
    # comment: the real tool env would read the user's config.toml.
    monkeypatch.setattr(claude_cli, "_tool_env", lambda: dict(os.environ))
    return config


def claude_session(session_id: str = "session-1") -> sessions.Session:
    session = sessions.get_session(CHAT_KEY, session_id=session_id)
    messages_json = sessions.get_messages(session)
    messages_json["backend"] = "claude"
    sessions.set_messages(session, messages_json)
    return session
