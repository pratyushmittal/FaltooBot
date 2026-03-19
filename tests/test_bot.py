import asyncio
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from neonize.aioze.client import NewAClient
from neonize.aioze.events import MessageEv
from neonize.proto import Neonize_pb2
from neonize.utils.jid import Jid2String
from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import AudioMessage, Message
from neonize.utils.enum import ChatPresence, ChatPresenceMedia

from faltoobot import audio, bot
from faltoobot.bot import keep_chat_typing, source_chat_ids
from faltoobot.config import Config
from faltoobot.store import whatsapp_session


def make_config(tmp_path: Path, *, allowed_chats: set[str]) -> Config:
    home = tmp_path / "home"
    root = home / ".faltoobot"
    return Config(
        home=home,
        root=root,
        config_file=root / "config.toml",
        log_file=root / "faltoobot.log",
        sessions_dir=root / "sessions",
        session_db=root / "session.db",
        launch_agent=root / "launch-agent.plist",
        run_script=root / "run.sh",
        openai_api_key="",
        openai_model="gpt-5.4",
        openai_thinking="high",
        openai_fast=False,
        openai_transcription_model="gpt-4o-transcribe",
        system_prompt="",
        transcription_prompt="Prefer English script.",
        allow_groups=False,
        allowed_chats=allowed_chats,
    )


def jid(user: str, server: str) -> Neonize_pb2.JID:
    return Neonize_pb2.JID(User=user, Server=server)


def fake_event(
    *, message_id: str = "msg-1", text: str = "", audio_seconds: int = 0
) -> MessageEv:
    source = Neonize_pb2.MessageSource(
        Chat=jid("15555555555555", "lid"),
        Sender=jid("15555555555555", "lid"),
    )
    message = Message(conversation=text)
    if audio_seconds:
        message.audioMessage.CopyFrom(
            AudioMessage(mimetype="audio/ogg", seconds=audio_seconds, PTT=True)
        )
    return cast(
        MessageEv,
        SimpleNamespace(
            Message=message,
            Info=SimpleNamespace(MessageSource=source, ID=message_id),
        ),
    )


class FakePresenceClient:
    def __init__(self, audio_bytes: bytes = b"voice-note") -> None:
        self.audio_bytes = audio_bytes
        self.calls: list[tuple[str, str]] = []
        self.replies: list[str] = []
        self.sent_messages: list[str] = []
        self.downloads = 0

    async def send_chat_presence(
        self,
        jid: Neonize_pb2.JID,
        state: ChatPresence,
        media: ChatPresenceMedia,
    ) -> str:
        self.calls.append((state.name, media.name))
        return "ok"

    async def reply_message(self, text: str, event: object) -> str:
        self.replies.append(text)
        return "ok"

    async def send_message(self, chat: Neonize_pb2.JID, text: str) -> str:
        self.sent_messages.append(text)
        return "ok"

    async def download_any(self, message: Message, path: str | None = None) -> bytes:
        self.downloads += 1
        return self.audio_bytes


def test_source_chat_ids_include_alt_phone_identity() -> None:
    source = Neonize_pb2.MessageSource(
        Chat=jid("15555555555555", "lid"),
        Sender=jid("15555555555555", "lid"),
        SenderAlt=jid("15555550123", "s.whatsapp.net"),
    )

    assert bot.source_chat_ids(source) == {
        "15555555555555@lid",
        "15555550123@s.whatsapp.net",
    }


def test_allowlist_matches_sender_alt_phone_identity(tmp_path: Path) -> None:
    source = Neonize_pb2.MessageSource(
        Chat=jid("15555555555555", "lid"),
        Sender=jid("15555555555555", "lid"),
        SenderAlt=jid("15555550123", "s.whatsapp.net"),
    )
    config = make_config(tmp_path, allowed_chats={"15555550123@s.whatsapp.net"})

    assert bot.is_allowed_chat(config, source) is True


def test_allowlist_matches_phone_without_country_code(tmp_path: Path) -> None:
    source = Neonize_pb2.MessageSource(
        Chat=jid("15555555555555", "lid"),
        Sender=jid("15555555555555", "lid"),
        SenderAlt=jid("15555550123", "s.whatsapp.net"),
    )
    config = make_config(tmp_path, allowed_chats={"15555550123@s.whatsapp.net"})

    assert bot.is_allowed_chat(config, source) is True


def test_keep_chat_typing_sends_composing_then_paused() -> None:
    async def run() -> list[tuple[str, str]]:
        client = FakePresenceClient()
        stop = asyncio.Event()
        task = asyncio.create_task(
            keep_chat_typing(
                cast(NewAClient, client), jid("15555550123", "s.whatsapp.net"), stop
            )
        )

        await asyncio.sleep(0)
        stop.set()
        await task
        return client.calls

    assert asyncio.run(run()) == [
        ("CHAT_PRESENCE_COMPOSING", "CHAT_PRESENCE_MEDIA_TEXT"),
        ("CHAT_PRESENCE_PAUSED", "CHAT_PRESENCE_MEDIA_TEXT"),
    ]


def test_source_chat_ids_strip_device_suffixes() -> None:
    source = Neonize_pb2.MessageSource(
        Chat=jid("55555555555555", "lid"),
        Sender=jid("55555555555555:4", "lid"),
        SenderAlt=jid("15555550123:4", "s.whatsapp.net"),
    )

    assert source_chat_ids(source) == {
        "55555555555555@lid",
        "15555550123@s.whatsapp.net",
    }


@pytest.mark.anyio
async def test_process_message_transcribes_voice_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path, allowed_chats=set())
    client = FakePresenceClient()

    async def fake_transcribe_audio(
        openai_client: object,
        audio_bytes: bytes,
        *,
        mimetype: str,
        prompt: str,
        model: str,
    ) -> str:
        assert audio_bytes == b"voice-note"
        assert mimetype == "audio/ogg"
        assert prompt == "Prefer English script."
        assert model == "gpt-4o-transcribe"
        return "Call mom at 6"

    async def fake_reply(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "text": "Done",
            "output_items": [],
            "usage": None,
            "instructions": "sys",
        }

    monkeypatch.setattr(audio, "transcribe_audio", fake_transcribe_audio)
    monkeypatch.setattr(bot, "reply", fake_reply)

    event = fake_event(audio_seconds=7)
    await bot.process_message(
        cast(NewAClient, client),
        event,
        config=config,
        openai_client=object(),
        chat_locks=defaultdict(asyncio.Lock),
        session_index_lock=asyncio.Lock(),
    )

    session = whatsapp_session(
        config.sessions_dir, Jid2String(event.Info.MessageSource.Chat)
    )
    assert client.downloads == 1
    assert client.replies == ["Done"]
    assert [turn.content for turn in session.messages] == ["Call mom at 6", "Done"]


@pytest.mark.anyio
async def test_process_message_rejects_long_voice_notes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = make_config(tmp_path, allowed_chats=set())
    client = FakePresenceClient()

    async def fake_reply(*args: object, **kwargs: object) -> dict[str, object]:
        raise AssertionError("reply should not run for oversized voice notes")

    monkeypatch.setattr(bot, "reply", fake_reply)

    event = fake_event(audio_seconds=audio.DEFAULT_AUDIO_MAX_SECONDS + 1)
    await bot.process_message(
        cast(NewAClient, client),
        event,
        config=config,
        openai_client=object(),
        chat_locks=defaultdict(asyncio.Lock),
        session_index_lock=asyncio.Lock(),
    )

    session = whatsapp_session(
        config.sessions_dir, Jid2String(event.Info.MessageSource.Chat)
    )
    assert client.downloads == 0
    assert client.replies == [
        f"Voice note is too long. Keep it under {audio.DEFAULT_AUDIO_MAX_SECONDS} seconds."
    ]
    assert session.messages == ()


@pytest.mark.anyio
async def test_audio_prompt_normalizes_urdu_script() -> None:
    client = FakePresenceClient()
    event = fake_event(audio_seconds=7)
    calls: list[str] = []

    class FakeTranscriptions:
        async def create(self, **kwargs: object) -> str:
            calls.append("transcribe")
            return "ہیلو دنیا"

    class FakeResponses:
        async def create(self, **kwargs: object) -> SimpleNamespace:
            calls.append("normalize")
            assert kwargs["instructions"] == audio.SCRIPT_NORMALIZATION_PROMPT
            assert kwargs["input"] == "ہیلو دنیا"
            return SimpleNamespace(output_text="hello duniya")

    openai_client = SimpleNamespace(
        audio=SimpleNamespace(transcriptions=FakeTranscriptions()),
        responses=FakeResponses(),
    )

    transcript = await audio.audio_prompt(
        client,
        event,
        openai_client,
        transcription_prompt="Use English letters only.",
        normalization_model="gpt-5.4",
    )

    assert transcript == "hello duniya"
    assert calls == ["transcribe", "normalize"]
