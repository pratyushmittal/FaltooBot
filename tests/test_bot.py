import asyncio
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from neonize.aioze.client import NewAClient
from neonize.aioze.events import MessageEv
from neonize.proto import Neonize_pb2
from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import AudioMessage, Message
from neonize.utils.enum import ChatPresence, ChatPresenceMedia
from neonize.utils.jid import Jid2String

from faltoobot.whatsapp import audio, runtime
from faltoobot.whatsapp.runtime import (
    keep_chat_typing,
    latest_assistant_text,
    source_chat_ids,
)
from faltoobot.config import Config, normalize_chat
from faltoobot.sessions import get_messages, get_session


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

    assert runtime.source_chat_ids(source) == {
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

    assert runtime.is_allowed_chat(config, runtime.source_chat_ids(source)) is True


def test_allowlist_matches_phone_without_country_code(tmp_path: Path) -> None:
    source = Neonize_pb2.MessageSource(
        Chat=jid("15555555555555", "lid"),
        Sender=jid("15555555555555", "lid"),
        SenderAlt=jid("15555550123", "s.whatsapp.net"),
    )
    config = make_config(tmp_path, allowed_chats={"15555550123@s.whatsapp.net"})

    assert runtime.is_allowed_chat(config, runtime.source_chat_ids(source)) is True


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
    monkeypatch.setattr("faltoobot.sessions.app_root", lambda: tmp_path / ".faltoobot")
    config = make_config(tmp_path, allowed_chats=set())
    client = FakePresenceClient()
    prompts: list[str] = []

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

    async def fake_get_answer(*, question: str, **_: object) -> dict[str, Any]:
        prompts.append(question)
        return {
            "messages": [
                {"type": "message", "role": "user", "content": question},
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Done"}],
                },
            ]
        }

    monkeypatch.setattr(audio, "transcribe_audio", fake_transcribe_audio)
    monkeypatch.setattr(runtime, "get_answer", fake_get_answer)

    event = fake_event(audio_seconds=7)
    await runtime.process_message(
        cast(NewAClient, client),
        event,
        config=config,
        chat_locks=defaultdict(asyncio.Lock),
    )

    chat_key = normalize_chat(Jid2String(event.Info.MessageSource.Chat))
    session = get_session(chat_key=chat_key)
    assert client.downloads == 1
    assert client.replies == ["Done"]
    assert prompts == ["Call mom at 6"]
    assert get_messages(session)["message_ids"] == [event.Info.ID]


@pytest.mark.anyio
async def test_process_message_rejects_long_voice_notes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("faltoobot.sessions.app_root", lambda: tmp_path / ".faltoobot")
    config = make_config(tmp_path, allowed_chats=set())
    client = FakePresenceClient()

    async def fake_get_answer(*args: object, **kwargs: object) -> dict[str, object]:
        raise AssertionError("get_answer should not run for oversized voice notes")

    monkeypatch.setattr(runtime, "get_answer", fake_get_answer)

    event = fake_event(audio_seconds=audio.DEFAULT_AUDIO_MAX_SECONDS + 1)
    await runtime.process_message(
        cast(NewAClient, client),
        event,
        config=config,
        chat_locks=defaultdict(asyncio.Lock),
    )

    chat_key = normalize_chat(Jid2String(event.Info.MessageSource.Chat))
    session = get_session(chat_key=chat_key)
    assert client.downloads == 0
    assert client.replies == [
        f"Voice note is too long. Keep it under {audio.DEFAULT_AUDIO_MAX_SECONDS} seconds."
    ]
    assert get_messages(session)["message_ids"] == [event.Info.ID]


@pytest.mark.anyio
async def test_audio_prompt_normalizes_urdu_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    async def fake_close() -> None:
        return None

    openai_client = SimpleNamespace(
        audio=SimpleNamespace(transcriptions=FakeTranscriptions()),
        responses=FakeResponses(),
        close=fake_close,
    )

    monkeypatch.setattr(audio, "AsyncOpenAI", lambda api_key=None: openai_client)

    transcript = await audio.audio_prompt(
        client,
        event,
        openai_api_key="key",
        transcription_prompt="Use English letters only.",
        normalization_model="gpt-5.4",
    )

    assert transcript == "hello duniya"
    assert calls == ["transcribe", "normalize"]


def test_latest_assistant_text_reads_sessions_messages() -> None:
    messages_json = {
        "messages": [
            {"type": "message", "role": "user", "content": "hi"},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "hello"}],
            },
        ]
    }

    assert latest_assistant_text(messages_json) == "hello"


@pytest.mark.anyio
async def test_process_message_reset_creates_new_session_for_chat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("faltoobot.sessions.app_root", lambda: tmp_path / ".faltoobot")

    config = make_config(tmp_path, allowed_chats=set())
    config.root.mkdir(parents=True, exist_ok=True)
    client = FakePresenceClient()
    chat_key = "8960294979@s.whatsapp.net"
    first = get_session(chat_key=chat_key)
    original = get_messages(first)
    original["messages"].append({"type": "message", "role": "user", "content": "hi"})
    original["message_ids"] = ["msg-1"]
    from faltoobot.sessions import set_messages

    set_messages(first, original)

    source = Neonize_pb2.MessageSource(
        Chat=jid("8960294979", "s.whatsapp.net"),
        Sender=jid("8960294979", "s.whatsapp.net"),
    )
    event = cast(
        MessageEv,
        SimpleNamespace(
            Message=Message(conversation="/reset"),
            Info=SimpleNamespace(MessageSource=source, ID="msg-2"),
        ),
    )

    await runtime.process_message(
        cast(NewAClient, client),
        event,
        config=config,
        chat_locks=defaultdict(asyncio.Lock),
    )

    second = get_session(chat_key=chat_key)
    assert second != first
    assert client.replies == ["Memory cleared for this chat."]
    assert get_messages(first)["messages"] == [
        {"type": "message", "role": "user", "content": "hi"}
    ]
    assert get_messages(second)["messages"] == []
    assert get_messages(second)["message_ids"] == ["msg-1", "msg-2"]
