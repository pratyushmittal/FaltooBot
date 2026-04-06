import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from neonize.aioze.events import ConnectedEv, PairStatusEv

from faltoobot.config import Config
from faltoobot.whatsapp import login


class FakeClient:
    def __init__(self) -> None:
        self.handlers: dict[object, Any] = {}
        self.connected = False
        self.stopped = False

    def event(self, event_type: object):
        def decorator(handler):
            self.handlers[event_type] = handler
            return handler

        return decorator

    async def connect(self) -> None:
        self.connected = True
        pair_status = self.handlers.get(PairStatusEv)
        if pair_status is not None:
            await pair_status(self, cast(Any, SimpleNamespace(Status="paired")))
        connected = self.handlers.get(ConnectedEv)
        if connected is not None:
            await connected(self, cast(Any, SimpleNamespace()))

    async def stop(self) -> None:
        self.stopped = True


def make_config(tmp_path: Path) -> Config:
    root = tmp_path / ".faltoobot"
    return Config(
        home=tmp_path,
        root=root,
        config_file=root / "config.toml",
        log_file=root / "faltoobot.log",
        sessions_dir=root / "sessions",
        session_db=root / "session.db",
        launch_agent=root / "launch-agent.plist",
        run_script=root / "run.sh",
        openai_api_key="",
        openai_oauth="",
        openai_model="gpt-5.4",
        openai_thinking="high",
        openai_fast=False,
        openai_transcription_model="gpt-4o-transcribe",
        allow_groups=False,
        allowed_chats=set(),
        bot_name="Faltoo",
    )


@pytest.mark.anyio
async def test_wait_for_login_connects_and_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    original_sleep = asyncio.sleep
    monkeypatch.setattr(login.asyncio, "sleep", lambda _: original_sleep(0))

    await login.wait_for_login(cast(Any, client))

    assert client.connected is True
    assert client.stopped is True


def test_configure_logging_quiets_whatsapp_loggers(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "faltoobot.log"

    login.configure_logging(log_path)

    assert log_path.parent.is_dir()
    assert logging.getLogger("whatsmeow").level == logging.ERROR
    assert logging.getLogger("whatsmeow.Client.Socket").level == logging.ERROR


@pytest.mark.anyio
async def test_run_auth_uses_config_and_waits_for_login(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    calls: list[Any] = []

    monkeypatch.setattr(
        login, "configure_logging", lambda log_path: calls.append(log_path)
    )
    monkeypatch.setattr(
        login, "NewAClient", lambda session_db: calls.append(session_db) or "client"
    )

    async def fake_wait_for_login(client: object) -> None:
        calls.append(client)

    monkeypatch.setattr(login, "wait_for_login", fake_wait_for_login)

    await login.run_auth(config)

    assert calls == [config.log_file, str(config.session_db), "client"]
