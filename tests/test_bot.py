import asyncio
from collections import defaultdict
from importlib.metadata import version as package_version
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from neonize.aioze.client import NewAClient
from neonize.aioze.events import MessageEv
from neonize.proto import Neonize_pb2
from neonize.proto.waCommon.WACommon_pb2 import MessageKey
from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import (
    AlbumMessage,
    AudioMessage,
    ContextInfo,
    ExtendedTextMessage,
    ImageMessage,
    Message,
    MessageAssociation,
    MessageContextInfo,
)
from neonize.utils.enum import ChatPresence, ChatPresenceMedia
from neonize.utils.jid import Jid2String, build_jid
from PIL import Image

from faltoobot.config import (
    Config,
    default_config,
    normalize_chat,
    render_config,
)
from faltoobot import sessions
from faltoobot.sessions import get_messages, get_session, set_messages
from faltoobot.whatsapp import app as whatsapp_app
from faltoobot.whatsapp import audio, runtime
from faltoobot.whatsapp.runtime import keep_chat_typing, source_chat_ids


def make_config(
    tmp_path: Path,
    *,
    allowed_chats: set[str],
    allow_group_chats: set[str] | None = None,
) -> Config:
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
        openai_oauth="",
        openai_model="gpt-5.4",
        openai_thinking="high",
        openai_fast=False,
        openai_transcription_model="gpt-4o-transcribe",
        allow_group_chats=set() if allow_group_chats is None else allow_group_chats,
        allowed_chats=allowed_chats,
        bot_name="Faltoo",
        browser_binary="",
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


def fake_group_event(  # noqa: PLR0913
    *,
    message_id: str = "group-1",
    text: str = "hi",
    sender_phone: str = "15555550123",
    mentioned_jids: list[str] | None = None,
    reply: tuple[str, str] | None = None,
) -> MessageEv:
    source = Neonize_pb2.MessageSource(
        Chat=jid("120363000000000000", "g.us"),
        Sender=jid("15555555555555", "lid"),
        SenderAlt=jid(sender_phone, "s.whatsapp.net"),
        IsGroup=True,
    )
    message = Message()
    if mentioned_jids or reply:
        context_info = ContextInfo(mentionedJID=list(mentioned_jids or []))
        if reply:
            participant, quoted_text = reply
            context_info.participant = participant
            context_info.remoteJID = "120363000000000000@g.us"
            context_info.quotedMessage.CopyFrom(Message(conversation=quoted_text))
        message.extendedTextMessage.CopyFrom(
            ExtendedTextMessage(
                text=text,
                contextInfo=context_info,
            )
        )
    else:
        message.conversation = text
    return cast(
        MessageEv,
        SimpleNamespace(
            Message=message,
            Info=SimpleNamespace(MessageSource=source, ID=message_id),
        ),
    )


def fake_image_event(*, message_id: str = "img-1", caption: str = "") -> MessageEv:
    source = Neonize_pb2.MessageSource(
        Chat=jid("15555555555555", "lid"),
        Sender=jid("15555555555555", "lid"),
    )
    message = Message()
    message.imageMessage.CopyFrom(ImageMessage(mimetype="image/png", caption=caption))
    return cast(
        MessageEv,
        SimpleNamespace(
            Message=message,
            Info=SimpleNamespace(MessageSource=source, ID=message_id),
        ),
    )


def fake_album_event(*, message_id: str = "album-1", images: int = 2) -> MessageEv:
    source = Neonize_pb2.MessageSource(
        Chat=jid("15555555555555", "lid"),
        Sender=jid("15555555555555", "lid"),
    )
    message = Message()
    message.albumMessage.CopyFrom(AlbumMessage(expectedImageCount=images))
    return cast(
        MessageEv,
        SimpleNamespace(
            Message=message,
            Info=SimpleNamespace(MessageSource=source, ID=message_id),
        ),
    )


def _quoted_context(quoted_text: str) -> ContextInfo:
    return ContextInfo(
        stanzaID="quoted-1",
        participant="15555555555555@lid",
        remoteJID="15555555555555@lid",
        quotedMessage=Message(conversation=quoted_text),
    )


def fake_reply_text_event(
    *,
    message_id: str = "reply-1",
    text: str,
    quoted_text: str,
) -> MessageEv:
    source = Neonize_pb2.MessageSource(
        Chat=jid("15555555555555", "lid"),
        Sender=jid("15555555555555", "lid"),
    )
    message = Message()
    message.extendedTextMessage.CopyFrom(
        ExtendedTextMessage(text=text, contextInfo=_quoted_context(quoted_text))
    )
    return cast(
        MessageEv,
        SimpleNamespace(
            Message=message,
            Info=SimpleNamespace(MessageSource=source, ID=message_id),
        ),
    )


def fake_reply_audio_event(
    *,
    message_id: str = "voice-reply-1",
    quoted_text: str,
    audio_seconds: int = 7,
) -> MessageEv:
    source = Neonize_pb2.MessageSource(
        Chat=jid("15555555555555", "lid"),
        Sender=jid("15555555555555", "lid"),
    )
    message = Message()
    message.audioMessage.CopyFrom(
        AudioMessage(
            mimetype="audio/ogg",
            seconds=audio_seconds,
            PTT=True,
            contextInfo=_quoted_context(quoted_text),
        )
    )
    return cast(
        MessageEv,
        SimpleNamespace(
            Message=message,
            Info=SimpleNamespace(MessageSource=source, ID=message_id),
        ),
    )


def fake_album_child_event(
    *,
    message_id: str,
    parent_id: str,
    caption: str = "",
) -> MessageEv:
    source = Neonize_pb2.MessageSource(
        Chat=jid("15555555555555", "lid"),
        Sender=jid("15555555555555", "lid"),
    )
    message = Message()
    message.imageMessage.CopyFrom(ImageMessage(mimetype="image/png", caption=caption))
    message.messageContextInfo.CopyFrom(
        MessageContextInfo(
            messageAssociation=MessageAssociation(
                associationType=MessageAssociation.MEDIA_ALBUM,
                parentMessageKey=MessageKey(
                    remoteJID="15555555555555@lid",
                    fromMe=True,
                    ID=parent_id,
                ),
            )
        )
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
        self.reply_ids: list[str] = []
        self.sent_messages: list[str] = []
        self.sent_images: list[dict[str, object | None]] = []
        self.sent_documents: list[dict[str, object | None]] = []
        self.downloads = 0
        self.get_me_calls = 0

    async def get_me(self) -> Neonize_pb2.Device:
        self.get_me_calls += 1
        return Neonize_pb2.Device(
            JID=jid("15555550999", "s.whatsapp.net"),
            LID=jid("15555550999", "lid"),
        )

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
        self.reply_ids.append(str(getattr(getattr(event, "Info", None), "ID", "")))
        return "ok"

    async def send_message(self, chat: Neonize_pb2.JID, text: str) -> str:
        self.sent_messages.append(text)
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

    async def send_document(  # noqa: PLR0913
        self,
        chat: Neonize_pb2.JID,
        file: str | bytes,
        caption: str | None = None,
        filename: str | None = None,
        mimetype: str | None = None,
        quoted: object | None = None,
        **_: object,
    ) -> str:
        self.sent_documents.append(
            {
                "file": str(file),
                "caption": caption,
                "filename": filename,
                "mimetype": mimetype,
                "quoted": quoted,
            }
        )
        return "ok"

    async def download_any(self, message: Message, path: str | None = None) -> bytes:
        self.downloads += 1
        return self.audio_bytes


async def handle_message(
    client: NewAClient,
    event: MessageEv,
    *,
    config: Config,
    chat_locks: dict[str, asyncio.Lock] | None = None,
    pending_albums: dict[str, runtime.PendingAlbum] | None = None,
) -> None:
    chat_locks = defaultdict(asyncio.Lock) if chat_locks is None else chat_locks
    source = event.Info.MessageSource
    chat_jid = Jid2String(source.Chat)
    session = get_session(chat_key=normalize_chat(chat_jid))
    async with chat_locks[chat_jid]:
        turn = await runtime.get_turn_locked(
            client,
            event,
            config=config,
            session=session,
            pending_albums=pending_albums,
        )
        if turn is None:
            return
        stored = True
        if turn["prompt"] not in runtime.SLASH_COMMANDS:
            stored = await sessions.append_user_turn(
                session,
                question=turn["prompt"],
                attachments=turn["attachments"] or None,
                message_ids=turn["message_ids"],
            )
        if stored and not await runtime.is_unmentioned_group_message(
            client, turn["event"]
        ):
            await runtime.process_turn_locked(client, session, config=config, turn=turn)


def png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (4, 3), color="red").save(buffer, format="PNG")
    return buffer.getvalue()


def recording_append_user_turn(calls: list[dict[str, Any]]):
    async def fake_append_user_turn(
        session,
        *,
        question: str,
        attachments: list[Path] | None = None,
        message_ids: list[str] | tuple[str, ...] = (),
    ) -> bool:
        messages_json = get_messages(session)
        messages_json["messages"].append(
            {
                "type": "message",
                "role": "user",
                "content": question,
            }
        )
        messages_json["message_ids"].extend(message_ids)
        set_messages(session, messages_json)
        calls.append({"question": question, "attachments": list(attachments or [])})
        return True

    return fake_append_user_turn


def test_sender_id_prefers_primary_sender_for_mention_linkage() -> None:
    event = SimpleNamespace(
        Info=SimpleNamespace(
            MessageSource=Neonize_pb2.MessageSource(
                Sender=jid("15555555555555", "lid"),
                SenderAlt=jid("15555550123", "s.whatsapp.net"),
            )
        )
    )

    assert runtime._sender_id(cast(MessageEv, event)) == "15555555555555"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            Neonize_pb2.MessageSource(
                Chat=jid("15555555555555", "lid"),
                Sender=jid("15555555555555", "lid"),
                SenderAlt=jid("15555550123", "s.whatsapp.net"),
            ),
            {"15555555555555@lid", "15555550123@s.whatsapp.net"},
        ),
        (
            Neonize_pb2.MessageSource(
                Chat=jid("55555555555555", "lid"),
                Sender=jid("55555555555555:4", "lid"),
                SenderAlt=jid("15555550123:4", "s.whatsapp.net"),
            ),
            {"55555555555555@lid", "15555550123@s.whatsapp.net"},
        ),
    ],
)
def test_source_chat_ids_normalize_whatsapp_ids(
    source: Neonize_pb2.MessageSource, expected: set[str]
) -> None:
    assert source_chat_ids(source) == expected


@pytest.mark.parametrize(
    ("source", "allowed_chats", "expected"),
    [
        (
            Neonize_pb2.MessageSource(
                Chat=jid("15555555555555", "lid"),
                Sender=jid("15555555555555", "lid"),
                SenderAlt=jid("15555550123", "s.whatsapp.net"),
            ),
            {"15555550123@s.whatsapp.net"},
            True,
        ),
        (
            Neonize_pb2.MessageSource(
                Chat=jid("120363000000000000", "g.us"),
                Sender=jid("15555555555555", "lid"),
                SenderAlt=jid("15555550123", "s.whatsapp.net"),
                IsGroup=True,
            ),
            {"15555550123@s.whatsapp.net"},
            True,
        ),
    ],
)
def test_matches_allowed_chats(
    source: Neonize_pb2.MessageSource,
    allowed_chats: set[str],
    expected: bool,
) -> None:
    assert (
        runtime._matches_allowed_chats(allowed_chats, runtime.source_chat_ids(source))
        is expected
    )


def test_empty_group_allowlist_blocks_group_messages() -> None:
    source = Neonize_pb2.MessageSource(
        Chat=jid("120363000000000000", "g.us"),
        Sender=jid("15555555555555", "lid"),
        SenderAlt=jid("15555550123", "s.whatsapp.net"),
        IsGroup=True,
    )

    assert (
        bool(set())
        and runtime._matches_allowed_chats(set(), runtime.source_chat_ids(source))
    ) is False


@pytest.mark.anyio
async def test_get_turn_locked_uses_group_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("faltoobot.sessions.app_root", lambda: tmp_path / ".faltoobot")
    config = make_config(
        tmp_path,
        allowed_chats={"19999999999@s.whatsapp.net"},
        allow_group_chats={"15555550123@s.whatsapp.net"},
    )
    session = get_session(chat_key="120363000000000000@g.us")

    allowed_turn = await runtime.get_turn_locked(
        cast(NewAClient, FakePresenceClient()),
        fake_group_event(
            sender_phone="15555550123",
            mentioned_jids=["15555550999@s.whatsapp.net"],
        ),
        config=config,
        session=session,
    )
    blocked_turn = await runtime.get_turn_locked(
        cast(NewAClient, FakePresenceClient()),
        fake_group_event(message_id="group-2", sender_phone="16666660123"),
        config=config,
        session=session,
    )

    assert allowed_turn is not None
    assert allowed_turn["prompt"] == "[from 15555555555555] hi"
    assert blocked_turn is None


@pytest.mark.anyio
async def test_get_turn_locked_stores_group_messages_without_bot_mention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("faltoobot.sessions.app_root", lambda: tmp_path / ".faltoobot")
    config = make_config(
        tmp_path,
        allowed_chats=set(),
        allow_group_chats={"15555550123@s.whatsapp.net"},
    )
    session = get_session(chat_key="120363000000000000@g.us")

    turn = await runtime.get_turn_locked(
        cast(NewAClient, FakePresenceClient()),
        fake_group_event(sender_phone="15555550123", text="hello group"),
        config=config,
        session=session,
    )

    assert turn is not None
    assert turn["prompt"] == "[from 15555555555555] hello group"


@pytest.mark.anyio
async def test_get_turn_locked_allows_group_messages_when_bot_lid_is_mentioned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("faltoobot.sessions.app_root", lambda: tmp_path / ".faltoobot")
    config = make_config(
        tmp_path,
        allowed_chats=set(),
        allow_group_chats={"15555550123@s.whatsapp.net"},
    )
    session = get_session(chat_key="120363000000000000@g.us")
    client = FakePresenceClient()

    turn = await runtime.get_turn_locked(
        cast(NewAClient, client),
        fake_group_event(
            sender_phone="15555550123",
            text="hi @faltoo",
            mentioned_jids=["15555550999@lid"],
        ),
        config=config,
        session=session,
    )

    assert turn is not None
    assert turn["prompt"] == "[from 15555555555555] hi @faltoo"


@pytest.mark.anyio
async def test_group_follow_up_mention_sees_earlier_unmentioned_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("faltoobot.sessions.app_root", lambda: tmp_path / ".faltoobot")
    config = make_config(
        tmp_path,
        allowed_chats=set(),
        allow_group_chats={"15555550123@s.whatsapp.net"},
    )
    client = FakePresenceClient()
    seen: list[list[str]] = []

    async def fake_get_answer(session, **_: object) -> str:
        messages = get_messages(session)["messages"]
        seen.append(
            [
                str(message["content"])
                for message in messages
                if message["role"] == "user"
            ]
        )
        return "Done"

    monkeypatch.setattr(runtime, "get_answer", fake_get_answer)

    await handle_message(
        cast(NewAClient, client),
        fake_group_event(
            sender_phone="15555550123", text="We need to order milk today"
        ),
        config=config,
    )
    await handle_message(
        cast(NewAClient, client),
        fake_group_event(
            message_id="group-2",
            sender_phone="15555550123",
            text="@faltoo find the best way to do this",
            mentioned_jids=["15555550999@s.whatsapp.net"],
        ),
        config=config,
    )

    assert client.replies == ["Done"]
    assert seen == [
        [
            "[from 15555555555555] We need to order milk today",
            "[from 15555555555555] @faltoo find the best way to do this",
        ]
    ]


@pytest.mark.anyio
async def test_get_turn_locked_prefers_group_push_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("faltoobot.sessions.app_root", lambda: tmp_path / ".faltoobot")
    config = make_config(
        tmp_path,
        allowed_chats=set(),
        allow_group_chats={"15555550123@s.whatsapp.net"},
    )
    session = get_session(chat_key="120363000000000000@g.us")
    event = fake_group_event(sender_phone="15555550123", text="hello group")
    cast(Any, event.Info).PushName = "Aditya"

    turn = await runtime.get_turn_locked(
        cast(NewAClient, FakePresenceClient()),
        event,
        config=config,
        session=session,
    )

    assert turn is not None
    assert turn["prompt"] == "[from Aditya - 15555555555555] hello group"


@pytest.mark.anyio
async def test_get_turn_locked_keeps_group_slash_command_unprefixed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("faltoobot.sessions.app_root", lambda: tmp_path / ".faltoobot")
    config = make_config(
        tmp_path,
        allowed_chats=set(),
        allow_group_chats={"15555550123@s.whatsapp.net"},
    )
    session = get_session(chat_key="120363000000000000@g.us")

    turn = await runtime.get_turn_locked(
        cast(NewAClient, FakePresenceClient()),
        fake_group_event(sender_phone="15555550123", text="/status"),
        config=config,
        session=session,
    )

    assert turn is not None
    assert turn["prompt"] == "/status"


@pytest.mark.anyio
async def test_get_turn_locked_normalizes_addressed_group_slash_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("faltoobot.sessions.app_root", lambda: tmp_path / ".faltoobot")
    config = make_config(
        tmp_path,
        allowed_chats=set(),
        allow_group_chats={"15555550123@s.whatsapp.net"},
    )
    session = get_session(chat_key="120363000000000000@g.us")

    turn = await runtime.get_turn_locked(
        cast(NewAClient, FakePresenceClient()),
        fake_group_event(
            sender_phone="15555550123",
            text="@faltoo /status",
            mentioned_jids=["15555550999@s.whatsapp.net"],
        ),
        config=config,
        session=session,
    )

    assert turn is not None
    assert turn["prompt"] == "/status"


@pytest.mark.anyio
async def test_get_turn_locked_allows_group_messages_when_replying_to_bot_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("faltoobot.sessions.app_root", lambda: tmp_path / ".faltoobot")
    config = make_config(
        tmp_path,
        allowed_chats=set(),
        allow_group_chats={"15555550123@s.whatsapp.net"},
    )
    session = get_session(chat_key="120363000000000000@g.us")

    turn = await runtime.get_turn_locked(
        cast(NewAClient, FakePresenceClient()),
        fake_group_event(
            sender_phone="15555550123",
            text="thanks",
            reply=("15555550999@s.whatsapp.net", "Please share the file"),
        ),
        config=config,
        session=session,
    )

    assert turn is not None
    assert turn["prompt"] == (
        "[from 15555555555555]\n"
        "The user is replying to an earlier message.\n\n"
        "Earlier message:\n> Please share the file\n\n"
        "User reply:\nthanks"
    )
    assert turn["quoted_message_text"] == ""


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

    async def fake_get_answer(session, **_: object) -> str:
        prompts.append(str(get_messages(session)["messages"][-1]["content"]))
        return "Done"

    monkeypatch.setattr(audio, "transcribe_audio", fake_transcribe_audio)
    monkeypatch.setattr(runtime, "TRANSCRIPTION_PROMPT", "Prefer English script.")
    monkeypatch.setattr(runtime, "get_answer", fake_get_answer)

    event = fake_event(audio_seconds=7)
    await handle_message(
        cast(NewAClient, client),
        event,
        config=config,
        chat_locks=defaultdict(asyncio.Lock),
    )

    chat_key = normalize_chat(Jid2String(event.Info.MessageSource.Chat))
    session = get_session(chat_key=chat_key)
    assert client.downloads == 1
    assert client.replies == ["Done"]

    assert prompts == [
        "The user sent a voice note. "
        "The following text is a transcription of that voice note:\n\n"
        "Call mom at 6"
    ]
    assert get_messages(session)["message_ids"] == [event.Info.ID]


@pytest.mark.anyio
async def test_process_message_includes_reply_quote_text_in_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("faltoobot.sessions.app_root", lambda: tmp_path / ".faltoobot")
    config = make_config(tmp_path, allowed_chats=set())
    client = FakePresenceClient()
    prompts: list[str] = []

    async def fake_get_answer(session, **_: object) -> str:
        prompts.append(str(get_messages(session)["messages"][-1]["content"]))
        return "Done"

    monkeypatch.setattr(runtime, "get_answer", fake_get_answer)

    await handle_message(
        cast(NewAClient, client),
        fake_reply_text_event(
            text="Yes, do that", quoted_text="Please summarize the PDF"
        ),
        config=config,
        chat_locks=defaultdict(asyncio.Lock),
    )

    assert client.replies == ["Done"]
    assert prompts == [
        "The user is replying to an earlier message.\n\n"
        "Earlier message:\n> Please summarize the PDF\n\n"
        "User reply:\nYes, do that"
    ]


def test_prompt_with_reply_context_truncates_long_quotes() -> None:
    earlier = "x" * 510
    max_quoted_chars = 503

    prompt = runtime._prompt_with_reply_context("ok", earlier)

    assert "> " in prompt
    assert "...\n\nUser reply:\nok" in prompt
    assert (
        len(prompt.split("Earlier message:\n", 1)[1].split("\n\nUser reply:", 1)[0])
        <= max_quoted_chars
    )


@pytest.mark.anyio
async def test_process_message_includes_reply_quote_text_for_voice_notes(
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
        return "Sure, tomorrow morning"

    async def fake_get_answer(session, **_: object) -> str:
        prompts.append(str(get_messages(session)["messages"][-1]["content"]))
        return "Done"

    monkeypatch.setattr(audio, "transcribe_audio", fake_transcribe_audio)
    monkeypatch.setattr(runtime, "get_answer", fake_get_answer)

    await handle_message(
        cast(NewAClient, client),
        fake_reply_audio_event(quoted_text="Can you send the update by tomorrow?"),
        config=config,
        chat_locks=defaultdict(asyncio.Lock),
    )

    assert client.replies == ["Done"]
    assert prompts == [
        "The user is replying to an earlier message.\n\n"
        "Earlier message:\n> Can you send the update by tomorrow?\n\n"
        "User reply:\nThe user sent a voice note. "
        "The following text is a transcription of that voice note:\n\n"
        "Sure, tomorrow morning"
    ]


@pytest.mark.anyio
async def test_process_message_rejects_long_voice_notes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("faltoobot.sessions.app_root", lambda: tmp_path / ".faltoobot")
    config = make_config(tmp_path, allowed_chats=set())
    client = FakePresenceClient()

    async def fake_get_answer(*args: object, **kwargs: object) -> str:
        raise AssertionError(
            "get_answer_for_whatsapp should not run for oversized voice notes"
        )

    monkeypatch.setattr(runtime, "get_answer", fake_get_answer)

    event = fake_event(audio_seconds=audio.DEFAULT_AUDIO_MAX_SECONDS + 1)
    await handle_message(
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
    assert get_messages(session)["message_ids"] == []


@pytest.mark.anyio
async def test_audio_prompt_returns_transcript_without_second_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakePresenceClient()
    event = fake_event(audio_seconds=7)
    calls: list[str] = []

    class FakeTranscriptions:
        async def create(self, **kwargs: object) -> str:
            calls.append("transcribe")
            return "ہیلو دنیا"

    async def fake_close() -> None:
        return None

    openai_client = SimpleNamespace(
        audio=SimpleNamespace(transcriptions=FakeTranscriptions()),
        close=fake_close,
    )

    monkeypatch.setattr(audio, "AsyncOpenAI", lambda api_key=None: openai_client)

    transcript = await audio.audio_prompt(
        client,
        event,
        openai_api_key="key",
        transcription_prompt="Use English letters only.",
    )

    assert transcript == (
        "The user sent a voice note. "
        "The following text is a transcription of that voice note:\n\n"
        "ہیلو دنیا"
    )
    assert calls == ["transcribe"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("caption", "answer", "expected_question"),
    [
        ("what is in this image?", "nice cat", "what is in this image?"),
        ("", "looks good", ""),
    ],
)
async def test_process_message_handles_whatsapp_image_turns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caption: str,
    answer: str,
    expected_question: str,
) -> None:
    monkeypatch.setattr("faltoobot.sessions.app_root", lambda: tmp_path / ".faltoobot")
    config = make_config(tmp_path, allowed_chats=set())
    client = FakePresenceClient(audio_bytes=png_bytes())
    calls: list[dict[str, Any]] = []

    async def fake_get_answer(session, **_: object) -> str:
        return answer

    monkeypatch.setattr(sessions, "append_user_turn", recording_append_user_turn(calls))
    monkeypatch.setattr(runtime, "get_answer", fake_get_answer)

    await handle_message(
        cast(NewAClient, client),
        fake_image_event(caption=caption),
        config=config,
        chat_locks=defaultdict(asyncio.Lock),
    )

    assert client.downloads == 1
    assert client.replies == [answer]
    assert len(calls) == 1
    assert calls[0]["question"] == expected_question
    assert len(calls[0]["attachments"]) == 1
    assert calls[0]["attachments"][0].suffix == ".png"
    assert calls[0]["attachments"][0].is_file() is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case",
    [
        {
            "album_id": "album-1",
            "first_child": fake_album_child_event(
                message_id="img-1",
                parent_id="album-1",
                caption="compare these",
            ),
            "second_child": fake_album_child_event(
                message_id="img-2", parent_id="album-1"
            ),
            "expected_question": "compare these",
        },
        {
            "album_id": "album-2",
            "first_child": fake_album_child_event(
                message_id="img-3", parent_id="album-2"
            ),
            "second_child": fake_album_child_event(
                message_id="img-4", parent_id="album-2"
            ),
            "expected_question": "",
        },
    ],
)
async def test_process_message_groups_whatsapp_album_images_into_one_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: dict[str, object],
) -> None:
    monkeypatch.setattr("faltoobot.sessions.app_root", lambda: tmp_path / ".faltoobot")
    config = make_config(tmp_path, allowed_chats=set())
    client = FakePresenceClient(audio_bytes=png_bytes())
    calls: list[dict[str, Any]] = []
    pending_albums: dict[str, runtime.PendingAlbum] = {}
    chat_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
    expected_images = 2

    async def fake_get_answer(session, **_: object) -> str:
        return "done"

    monkeypatch.setattr(sessions, "append_user_turn", recording_append_user_turn(calls))
    monkeypatch.setattr(runtime, "get_answer", fake_get_answer)

    await handle_message(
        cast(NewAClient, client),
        fake_album_event(message_id=str(case["album_id"]), images=expected_images),
        config=config,
        chat_locks=chat_locks,
        pending_albums=pending_albums,
    )
    await handle_message(
        cast(NewAClient, client),
        cast(MessageEv, case["first_child"]),
        config=config,
        chat_locks=chat_locks,
        pending_albums=pending_albums,
    )
    assert calls == []
    assert client.replies == []

    await handle_message(
        cast(NewAClient, client),
        cast(MessageEv, case["second_child"]),
        config=config,
        chat_locks=chat_locks,
        pending_albums=pending_albums,
    )

    assert pending_albums == {}
    assert client.downloads == expected_images
    assert client.replies == ["done"]
    assert client.reply_ids == [str(case["album_id"])]
    assert len(calls) == 1
    assert calls[0]["question"] == str(case["expected_question"])
    assert len(calls[0]["attachments"]) == expected_images
    if case["expected_question"]:
        chat_key = normalize_chat("15555555555555@lid")
        session = get_session(chat_key=chat_key)
        assert get_messages(session)["message_ids"] == [
            str(case["album_id"]),
            "img-1",
            "img-2",
        ]


@pytest.mark.anyio
async def test_process_turn_locked_status_reports_version_and_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("faltoobot.sessions.app_root", lambda: tmp_path / ".faltoobot")
    client = FakePresenceClient()
    config = make_config(tmp_path, allowed_chats={"15555550123@s.whatsapp.net"})
    config.root.mkdir(parents=True, exist_ok=True)
    config.config_file.parent.mkdir(parents=True, exist_ok=True)

    data = default_config()
    data["openai"]["api_key"] = "sk-test"
    data["openai"]["model"] = "gpt-5.2-codex"
    data["browser"]["binary"] = (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )
    data["bot"]["allowed_chats"] = ["15555550123@s.whatsapp.net"]
    config.config_file.write_text(render_config(data), encoding="utf-8")

    async def fake_get_answer(*args: object, **kwargs: object) -> str:
        raise AssertionError("get_answer should not run for /status")

    monkeypatch.setattr(runtime, "get_answer", fake_get_answer)
    session = get_session(chat_key="15555550123@s.whatsapp.net")
    event = fake_event(message_id="status-1", text="/status")

    turn: runtime.Turn = {
        "event": event,
        "chat": jid("15555550123", "s.whatsapp.net"),
        "message_ids": ["status-1"],
        "prompt": "/status",
        "quoted_message_text": "",
        "attachments": [],
        "audio": None,
    }
    await runtime.process_turn_locked(
        cast(NewAClient, client),
        session,
        config=config,
        turn=turn,
    )
    assert client.replies == [
        "\n".join(
            [
                "Faltoobot status",
                "",
                f"Version: {package_version('faltoobot')}",
                "",
                "Config status",
                '• openai_api_key="<set>"',
                '• openai_oauth=""',
                '• openai_model="gpt-5.2-codex"',
                '• openai_thinking="high"',
                "• openai_fast=false",
                '• openai_transcription_model="gpt-4o-transcribe"',
                '• gemini_gemini_api_key=""',
                '• gemini_model="gemini-3.1-flash-image-preview"',
                '• ui_theme=""',
                (
                    '• browser_binary="/Applications/Google Chrome.app/Contents/MacOS/'
                    'Google Chrome"'
                ),
                "• bot_allow_group_chats=[]",
                '• bot_allowed_chats=["15555550123@s.whatsapp.net"]',
                '• bot_bot_name="Faltoo"',
            ]
        )
    ]
    assert client.reply_ids == ["status-1"]


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

    await handle_message(
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
    assert get_messages(second)["message_ids"] == ["msg-1"]


class _DummyClient:
    def event(self, _event: object):
        def decorator(function):
            return function

        return decorator

    async def connect(self) -> None:
        return None

    async def idle(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class FakeTimerHandle:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.was_cancelled = False

    def cancel(self) -> None:
        self.was_cancelled = True

    def cancelled(self) -> bool:
        return self.was_cancelled

    def fire(self) -> None:
        if not self.was_cancelled:
            self.callback()


class FakeDebounceLoop:
    def __init__(self) -> None:
        self.handles: list[tuple[float, FakeTimerHandle]] = []

    def call_later(self, delay: float, callback) -> FakeTimerHandle:
        handle = FakeTimerHandle(callback)
        self.handles.append((delay, handle))
        return handle


@pytest.mark.anyio
async def test_run_bot_allows_oauth_without_api_key(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path, allowed_chats=set())
    config.openai_oauth = "auth.json"

    monkeypatch.setattr(whatsapp_app.login, "configure_logging", lambda path: None)
    monkeypatch.setattr(whatsapp_app, "client", _DummyClient())

    class _DummyLoop:
        def add_signal_handler(self, *_: object) -> None:
            return None

    monkeypatch.setattr(whatsapp_app.asyncio, "get_running_loop", lambda: _DummyLoop())

    await whatsapp_app.main(config)


@pytest.mark.anyio
async def test_handle_message_uses_normalized_chat_key_for_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_key = "normalized@chat"
    monkeypatch.setattr(
        whatsapp_app, "config", make_config(tmp_path, allowed_chats=set())
    )
    monkeypatch.setattr(whatsapp_app, "chat_locks", defaultdict(asyncio.Lock))
    monkeypatch.setattr(whatsapp_app, "normalize_chat", lambda value: expected_key)
    monkeypatch.setattr(
        whatsapp_app, "get_session", lambda chat_key: (chat_key, "session-1")
    )

    async def fake_get_turn_locked(
        client: NewAClient,
        event: MessageEv,
        *,
        config: Config,
        session,
        pending_albums: dict[str, runtime.PendingAlbum] | None = None,
    ) -> None:
        return None

    monkeypatch.setattr(runtime, "get_turn_locked", fake_get_turn_locked)

    await whatsapp_app._handle_message(
        cast(NewAClient, FakePresenceClient()),
        fake_event(text="hello"),
    )

    assert list(whatsapp_app.chat_locks.keys()) == [expected_key]


@pytest.mark.anyio
async def test_handle_message_debounces_reply_until_timer_fires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = FakeDebounceLoop()
    event = fake_event(text="hello")
    turn: runtime.Turn = {
        "event": event,
        "chat": event.Info.MessageSource.Chat,
        "message_ids": [event.Info.ID],
        "prompt": "hello",
        "quoted_message_text": "",
        "attachments": [],
        "audio": None,
    }
    calls: list[str] = []

    monkeypatch.setattr(
        whatsapp_app, "config", make_config(tmp_path, allowed_chats=set())
    )
    monkeypatch.setattr(whatsapp_app, "chat_locks", defaultdict(asyncio.Lock))
    monkeypatch.setattr(whatsapp_app, "debounce_timers", {})
    monkeypatch.setattr(
        whatsapp_app, "normalize_chat", lambda value: "chat@s.whatsapp.net"
    )
    monkeypatch.setattr(
        whatsapp_app, "get_session", lambda chat_key: (chat_key, "session-1")
    )
    monkeypatch.setattr(whatsapp_app.asyncio, "get_running_loop", lambda: loop)

    async def fake_get_turn_locked(*args: object, **kwargs: object) -> runtime.Turn:
        return turn

    async def fake_store_turn_locked(*args: object, **kwargs: object) -> bool:
        calls.append("store")
        return True

    async def fake_process_turn_locked(*args: object, **kwargs: object) -> None:
        calls.append("process")

    monkeypatch.setattr(runtime, "get_turn_locked", fake_get_turn_locked)
    monkeypatch.setattr(whatsapp_app, "append_user_turn", fake_store_turn_locked)
    monkeypatch.setattr(runtime, "process_turn_locked", fake_process_turn_locked)

    await whatsapp_app._handle_message(cast(NewAClient, FakePresenceClient()), event)

    assert calls == ["store"]
    assert len(loop.handles) == 1
    assert loop.handles[0][0] == whatsapp_app.DEBOUNCE_SECONDS

    loop.handles[0][1].fire()
    await asyncio.sleep(0)

    assert calls == ["store", "process"]


@pytest.mark.anyio
async def test_handle_message_schedules_debounce_outside_chat_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = fake_event(text="hello")
    chat_key = "chat@s.whatsapp.net"
    turn: runtime.Turn = {
        "event": event,
        "chat": event.Info.MessageSource.Chat,
        "message_ids": [event.Info.ID],
        "prompt": "hello",
        "quoted_message_text": "",
        "attachments": [],
        "audio": None,
    }
    seen: list[bool] = []

    class LockCheckingLoop(FakeDebounceLoop):
        def call_later(self, delay: float, callback) -> FakeTimerHandle:
            seen.append(whatsapp_app.chat_locks[chat_key].locked())
            return super().call_later(delay, callback)

    loop = LockCheckingLoop()
    monkeypatch.setattr(
        whatsapp_app, "config", make_config(tmp_path, allowed_chats=set())
    )
    monkeypatch.setattr(whatsapp_app, "chat_locks", defaultdict(asyncio.Lock))
    monkeypatch.setattr(whatsapp_app, "debounce_timers", {})
    monkeypatch.setattr(whatsapp_app, "normalize_chat", lambda value: chat_key)
    monkeypatch.setattr(
        whatsapp_app, "get_session", lambda chat_key: (chat_key, "session-1")
    )
    monkeypatch.setattr(whatsapp_app.asyncio, "get_running_loop", lambda: loop)

    async def fake_get_turn_locked(*args: object, **kwargs: object) -> runtime.Turn:
        return turn

    async def fake_store_turn_locked(*args: object, **kwargs: object) -> bool:
        return True

    monkeypatch.setattr(runtime, "get_turn_locked", fake_get_turn_locked)
    monkeypatch.setattr(whatsapp_app, "append_user_turn", fake_store_turn_locked)

    await whatsapp_app._handle_message(cast(NewAClient, FakePresenceClient()), event)

    assert seen == [False]


@pytest.mark.anyio
async def test_handle_message_resets_existing_debounce_timer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = FakeDebounceLoop()
    events = [
        fake_event(message_id="msg-1", text="one"),
        fake_event(message_id="msg-2", text="two"),
    ]
    turns: list[runtime.Turn] = [
        {
            "event": events[0],
            "chat": events[0].Info.MessageSource.Chat,
            "message_ids": [events[0].Info.ID],
            "prompt": "one",
            "quoted_message_text": "",
            "attachments": [],
            "audio": None,
        },
        {
            "event": events[1],
            "chat": events[1].Info.MessageSource.Chat,
            "message_ids": [events[1].Info.ID],
            "prompt": "two",
            "quoted_message_text": "",
            "attachments": [],
            "audio": None,
        },
    ]
    processed: list[str] = []

    monkeypatch.setattr(
        whatsapp_app, "config", make_config(tmp_path, allowed_chats=set())
    )
    monkeypatch.setattr(whatsapp_app, "chat_locks", defaultdict(asyncio.Lock))
    monkeypatch.setattr(whatsapp_app, "debounce_timers", {})
    monkeypatch.setattr(
        whatsapp_app, "normalize_chat", lambda value: "chat@s.whatsapp.net"
    )
    monkeypatch.setattr(
        whatsapp_app, "get_session", lambda chat_key: (chat_key, "session-1")
    )
    monkeypatch.setattr(whatsapp_app.asyncio, "get_running_loop", lambda: loop)

    async def fake_get_turn_locked(*args: object, **kwargs: object) -> runtime.Turn:
        return turns.pop(0)

    async def fake_store_turn_locked(*args: object, **kwargs: object) -> bool:
        return True

    async def fake_process_turn_locked(*args: object, **kwargs: Any) -> None:
        processed.append(str(cast(runtime.Turn, kwargs["turn"])["prompt"]))

    monkeypatch.setattr(runtime, "get_turn_locked", fake_get_turn_locked)
    monkeypatch.setattr(whatsapp_app, "append_user_turn", fake_store_turn_locked)
    monkeypatch.setattr(runtime, "process_turn_locked", fake_process_turn_locked)

    await whatsapp_app._handle_message(
        cast(NewAClient, FakePresenceClient()), events[0]
    )
    first_handle = loop.handles[0][1]
    await whatsapp_app._handle_message(
        cast(NewAClient, FakePresenceClient()), events[1]
    )
    second_handle = loop.handles[1][1]

    assert first_handle.was_cancelled is True

    first_handle.fire()
    await asyncio.sleep(0)
    assert processed == []

    second_handle.fire()
    await asyncio.sleep(0)
    assert processed == ["two"]


@pytest.mark.anyio
async def test_debounce_timer_processes_under_same_chat_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = FakeDebounceLoop()
    event = fake_event(text="hello")
    turn: runtime.Turn = {
        "event": event,
        "chat": event.Info.MessageSource.Chat,
        "message_ids": [event.Info.ID],
        "prompt": "hello",
        "quoted_message_text": "",
        "attachments": [],
        "audio": None,
    }
    seen: list[bool] = []
    chat_key = "chat@s.whatsapp.net"

    monkeypatch.setattr(
        whatsapp_app, "config", make_config(tmp_path, allowed_chats=set())
    )
    monkeypatch.setattr(whatsapp_app, "chat_locks", defaultdict(asyncio.Lock))
    monkeypatch.setattr(whatsapp_app, "debounce_timers", {})
    monkeypatch.setattr(whatsapp_app, "normalize_chat", lambda value: chat_key)
    monkeypatch.setattr(
        whatsapp_app, "get_session", lambda chat_key: (chat_key, "session-1")
    )
    monkeypatch.setattr(whatsapp_app.asyncio, "get_running_loop", lambda: loop)

    async def fake_get_turn_locked(*args: object, **kwargs: object) -> runtime.Turn:
        return turn

    async def fake_store_turn_locked(*args: object, **kwargs: object) -> bool:
        return True

    async def fake_process_turn_locked(*args: object, **kwargs: object) -> None:
        seen.append(whatsapp_app.chat_locks[chat_key].locked())

    monkeypatch.setattr(runtime, "get_turn_locked", fake_get_turn_locked)
    monkeypatch.setattr(whatsapp_app, "append_user_turn", fake_store_turn_locked)
    monkeypatch.setattr(runtime, "process_turn_locked", fake_process_turn_locked)

    await whatsapp_app._handle_message(cast(NewAClient, FakePresenceClient()), event)
    loop.handles[0][1].fire()
    await asyncio.sleep(0)

    assert seen == [True]


@pytest.mark.anyio
async def test_handle_message_stores_turn_without_reply_when_not_addressed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = FakeDebounceLoop()
    event = fake_group_event(text="hello")
    turn: runtime.Turn = {
        "event": event,
        "chat": event.Info.MessageSource.Chat,
        "message_ids": [event.Info.ID],
        "prompt": "[from 15555550123] hello",
        "quoted_message_text": "",
        "attachments": [],
        "audio": None,
    }
    calls: list[str] = []

    monkeypatch.setattr(
        whatsapp_app, "config", make_config(tmp_path, allowed_chats=set())
    )
    monkeypatch.setattr(whatsapp_app, "chat_locks", defaultdict(asyncio.Lock))
    monkeypatch.setattr(whatsapp_app, "debounce_timers", {})
    monkeypatch.setattr(
        whatsapp_app, "normalize_chat", lambda value: "120363000000000000@g.us"
    )
    monkeypatch.setattr(
        whatsapp_app, "get_session", lambda chat_key: (chat_key, "session-1")
    )
    monkeypatch.setattr(whatsapp_app.asyncio, "get_running_loop", lambda: loop)

    async def fake_get_turn_locked(*args: object, **kwargs: object) -> runtime.Turn:
        return turn

    async def fake_store_turn_locked(*args: object, **kwargs: object) -> bool:
        calls.append("store")
        return True

    async def fake_process_turn_locked(*args: object, **kwargs: object) -> None:
        calls.append("process")

    async def fake_is_unmentioned_group_message(
        *args: object, **kwargs: object
    ) -> bool:
        return True

    monkeypatch.setattr(runtime, "get_turn_locked", fake_get_turn_locked)
    monkeypatch.setattr(whatsapp_app, "append_user_turn", fake_store_turn_locked)
    monkeypatch.setattr(runtime, "process_turn_locked", fake_process_turn_locked)
    monkeypatch.setattr(
        runtime, "is_unmentioned_group_message", fake_is_unmentioned_group_message
    )

    await whatsapp_app._handle_message(cast(NewAClient, FakePresenceClient()), event)

    assert calls == ["store"]
    assert loop.handles == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("answer", "expected_messages"),
    [("queued reply", ["queued reply"]), ("[noreply]", [])],
)
async def test_process_turn_locked_handles_notification_turn_answers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    answer: str,
    expected_messages: list[str],
) -> None:
    monkeypatch.setattr("faltoobot.sessions.app_root", lambda: tmp_path / ".faltoobot")
    client = FakePresenceClient()
    config = make_config(tmp_path, allowed_chats=set())
    seen: dict[str, object] = {}
    session = get_session(chat_key="15555550123@s.whatsapp.net")

    async def fake_get_answer(session, **_: object) -> str:
        seen["question"] = get_messages(session)["messages"][-1]["content"]
        return answer

    monkeypatch.setattr(runtime, "get_answer", fake_get_answer)
    turn: runtime.Turn = {
        "event": None,
        "chat": jid("15555550123", "s.whatsapp.net"),
        "message_ids": ["notify_1"],
        "prompt": "queued user message",
        "quoted_message_text": "",
        "attachments": [],
        "audio": None,
    }

    stored = await sessions.append_user_turn(
        session,
        question=turn["prompt"],
        attachments=turn["attachments"] or None,
        message_ids=turn["message_ids"],
    )
    assert stored is True
    await runtime.process_turn_locked(
        cast(NewAClient, client),
        session,
        config=config,
        turn=turn,
    )

    assert seen["question"] == "queued user message"
    assert client.sent_messages == expected_messages
    assert client.replies == []


@pytest.mark.anyio
async def test_start_polling_notifications_claims_and_acks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(whatsapp_app, "client", cast(NewAClient, FakePresenceClient()))
    monkeypatch.setattr(
        whatsapp_app, "config", make_config(tmp_path, allowed_chats=set())
    )
    monkeypatch.setattr(whatsapp_app, "chat_locks", defaultdict(asyncio.Lock))
    monkeypatch.setattr(whatsapp_app, "notifications_stop", asyncio.Event())
    monkeypatch.setattr(
        whatsapp_app.notify_queue,
        "claim_notifications",
        lambda matches: [
            (
                tmp_path / "notify.json",
                {
                    "id": "notify_1",
                    "chat_key": "15555550123@s.whatsapp.net",
                    "message": "queued user message",
                    "created_at": "2026-04-05T00:00:00+00:00",
                },
            )
        ],
    )

    async def fake_store_turn_locked(*args: object, **kwargs: Any) -> bool:
        calls.append(str(kwargs["question"]))
        return True

    async def fake_process_turn_locked(*args: object, **kwargs: Any) -> None:
        whatsapp_app.notifications_stop.set()

    monkeypatch.setattr(whatsapp_app, "append_user_turn", fake_store_turn_locked)
    monkeypatch.setattr(
        whatsapp_app.runtime, "process_turn_locked", fake_process_turn_locked
    )
    monkeypatch.setattr(
        whatsapp_app.notify_queue, "ack_notification", lambda path: calls.append("ack")
    )
    monkeypatch.setattr(
        whatsapp_app.notify_queue,
        "requeue_notification",
        lambda path: calls.append("requeue"),
    )

    await whatsapp_app._start_polling_notifications()

    assert calls == [
        "# Notification (not visible to user)\n\n"
        "Reply with [noreply] if no user-facing reply is needed.\n\n"
        "## message\nqueued user message",
        "ack",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case",
    [
        {
            "filename": "chart.png",
            "payload": png_bytes(),
            "text": "Here.\n![Chart](chart.png)",
            "sent_messages": ["Here."],
            "sent_images": [{"file": "chart.png", "caption": "Chart", "quoted": None}],
            "sent_documents": [],
        },
        {
            "filename": "report.pdf",
            "payload": b"%PDF-1.4",
            "text": "![Quarterly report](report.pdf)",
            "sent_messages": [],
            "sent_images": [],
            "sent_documents": [
                {
                    "file": "report.pdf",
                    "caption": "Quarterly report",
                    "filename": "report.pdf",
                    "mimetype": "application/pdf",
                    "quoted": None,
                }
            ],
        },
    ],
)
async def test_send_text_sends_local_media_markdown(
    tmp_path: Path,
    case: dict[str, object],
) -> None:
    client = FakePresenceClient()
    media = tmp_path / str(case["filename"])
    media.write_bytes(cast(bytes, case["payload"]))

    await runtime.send_text(
        cast(NewAClient, client),
        chat=build_jid("123", "s.whatsapp.net"),
        text=str(case["text"]),
        workspace=tmp_path,
    )

    assert client.sent_messages == cast(list[str], case["sent_messages"])
    assert client.sent_images == [
        {**item, "file": str(media)}
        for item in cast(list[dict[str, object | None]], case["sent_images"])
    ]
    assert client.sent_documents == [
        {**item, "file": str(media)}
        for item in cast(list[dict[str, object | None]], case["sent_documents"])
    ]


@pytest.mark.anyio
async def test_send_text_quotes_media_replies_with_the_full_event(
    tmp_path: Path,
) -> None:
    client = FakePresenceClient()
    image = tmp_path / "chart.png"
    image.write_bytes(png_bytes())
    event = fake_event(text="hello")

    await runtime.send_text(
        cast(NewAClient, client),
        chat=build_jid("123", "s.whatsapp.net"),
        text=f"![Chart]({image.name})",
        event=event,
        workspace=tmp_path,
    )

    assert client.sent_images == [
        {"file": str(image), "caption": "Chart", "quoted": event}
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("text", "create_image", "expected_messages"),
    [
        ("Look ![Missing](missing.png)", False, ["Look ![Missing](missing.png)"]),
        ("Here. ![Chart](chart.png)", True, ["Here. ![Chart](chart.png)"]),
    ],
)
async def test_send_text_keeps_non_standalone_media_markdown_as_text(
    tmp_path: Path,
    text: str,
    create_image: bool,
    expected_messages: list[str],
) -> None:
    client = FakePresenceClient()
    if create_image:
        (tmp_path / "chart.png").write_bytes(png_bytes())

    await runtime.send_text(
        cast(NewAClient, client),
        chat=build_jid("123", "s.whatsapp.net"),
        text=text,
        workspace=tmp_path,
    )

    assert client.sent_messages == expected_messages
    assert client.sent_images == []
    assert client.sent_documents == []
