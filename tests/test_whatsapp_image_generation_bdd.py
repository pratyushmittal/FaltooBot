import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from neonize.aioze.client import NewAClient
from neonize.proto import Neonize_pb2
from neonize.utils.enum import ChatPresence, ChatPresenceMedia
from neonize.utils.jid import build_jid
from pytest_bdd import given, scenarios, then, when

from faltoobot import sessions
from faltoobot.config import Config, build_config
from faltoobot.whatsapp import runtime

pytestmark = pytest.mark.external

scenarios("features/whatsapp_image_generation.feature")


class FakeWhatsAppClient:
    def __init__(self) -> None:
        self.sent_images: list[dict[str, object | None]] = []
        self.replies: list[str] = []

    async def send_chat_presence(
        self,
        jid: Neonize_pb2.JID,
        state: ChatPresence,
        media: ChatPresenceMedia,
    ) -> str:
        return "ok"

    async def reply_message(self, text: str, event: object) -> str:
        self.replies.append(text)
        return "ok"

    async def send_image(
        self,
        chat: Neonize_pb2.JID,
        file: str | bytes,
        caption: str | None = None,
        quoted: object | None = None,
        **_: object,
    ) -> str:
        self.sent_images.append(
            {"file": str(file), "caption": caption, "quoted": quoted}
        )
        return "ok"


@pytest.fixture
def whatsapp_image_ctx(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> dict[str, Any]:
    config = build_config()
    if not (config.openai_api_key or config.openai_oauth):
        raise RuntimeError("OpenAI auth must be configured to run WhatsApp image E2E.")

    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = sessions.get_session(
        chat_key="15555550123@lid",
        workspace=workspace,
    )
    return {
        "client": FakeWhatsAppClient(),
        "config": config,
        "session": session,
        "event": None,
    }


@given("a fake WhatsApp agent")
def fake_whatsapp_agent(whatsapp_image_ctx: dict[str, Any]) -> None:
    assert isinstance(whatsapp_image_ctx["client"], FakeWhatsAppClient)


@when("I ask WhatsApp to create an image of a cat")
def ask_whatsapp_for_cat_image(whatsapp_image_ctx: dict[str, Any]) -> None:
    session = cast(sessions.Session, whatsapp_image_ctx["session"])
    event = SimpleNamespace(Info=SimpleNamespace(ID="msg-1"))
    whatsapp_image_ctx["event"] = event
    turn: runtime.Turn = {
        "event": cast(Any, event),
        "chat": build_jid("15555550123", "lid"),
        "message_ids": ["msg-1"],
        "prompt": "create an image of a cat. Reply with only the image.",
        "quoted_message_text": "",
        "attachments": [],
        "audio": None,
    }

    asyncio.run(
        sessions.append_user_turn(
            session,
            question=turn["prompt"],
            message_ids=turn["message_ids"],
        )
    )
    asyncio.run(
        runtime.process_turn_locked(
            cast(NewAClient, whatsapp_image_ctx["client"]),
            session,
            config=cast(Config, whatsapp_image_ctx["config"]),
            turn=turn,
        )
    )


@then("I receive an image of a cat")
def receive_cat_image(whatsapp_image_ctx: dict[str, Any]) -> None:
    client = cast(FakeWhatsAppClient, whatsapp_image_ctx["client"])
    event = whatsapp_image_ctx["event"]
    workspace = Path(sessions.get_messages(whatsapp_image_ctx["session"])["workspace"])
    images = list((workspace / sessions.GENERATED_IMAGES_DIR).glob("*.png"))

    assert len(images) == 1
    assert images[0].stat().st_size > 0
    assert client.replies == []
    assert client.sent_images == [
        {
            "file": str(images[0]),
            "caption": "Generated image",
            "quoted": event,
        }
    ]
