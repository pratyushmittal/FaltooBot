import asyncio
import logging
from typing import Any, TypedDict, Unpack
from uuid import uuid4

from neonize.aioze.client import NewAClient
from neonize.aioze.events import MessageEv
from neonize.utils.enum import ChatPresence, ChatPresenceMedia
from neonize.utils.jid import Jid2String

from faltoobot.config import Config, normalize_chat
from faltoobot.sessions import (
    MessagesJson,
    get_answer,
    get_messages,
    get_session,
    set_messages,
)

from .audio import AudioError, audio_message, audio_prompt

logger = logging.getLogger("faltoobot")
MIN_ALLOWLIST_DIGITS = 8
TYPING_REFRESH_SECONDS = 4.0
MESSAGE_CHUNK_LIMIT = 3500
HELP_TEXT = (
    "Faltoobot is online.\n\n"
    "• Send any message to ask the model\n"
    "• /reset — clear this chat's memory\n"
    "• /help — show this help"
)


class ProcessMessageOptions(TypedDict):
    config: Config
    chat_locks: dict[str, asyncio.Lock]


def source_chat_ids(source: Any) -> set[str]:
    source_ids = {
        normalize_chat(Jid2String(jid))
        for jid in (source.Chat, source.Sender, source.SenderAlt, source.RecipientAlt)
    }
    return {source_id for source_id in source_ids if source_id}


def is_allowed_chat(config: Config, source_ids: set[str]) -> bool:
    if not config.allowed_chats:
        return True
    if not source_ids.isdisjoint(config.allowed_chats):
        return True

    for allowed_chat in config.allowed_chats:
        if not allowed_chat.endswith("@s.whatsapp.net"):
            continue
        allowed_phone = allowed_chat.split("@", 1)[0]
        if len(allowed_phone) < MIN_ALLOWLIST_DIGITS:
            continue

        for source_id in source_ids:
            if not source_id.endswith("@s.whatsapp.net"):
                continue
            source_phone = source_id.split("@", 1)[0]
            if len(source_phone) < MIN_ALLOWLIST_DIGITS:
                continue
            if allowed_phone.endswith(source_phone) or source_phone.endswith(
                allowed_phone
            ):
                return True

    return False


async def keep_chat_typing(client: NewAClient, chat: Any, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await client.send_chat_presence(
                chat,
                ChatPresence.CHAT_PRESENCE_COMPOSING,
                ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT,
            )
        except Exception:
            logger.debug("Failed to update chat presence", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=TYPING_REFRESH_SECONDS)
        except TimeoutError:
            continue
    try:
        await client.send_chat_presence(
            chat,
            ChatPresence.CHAT_PRESENCE_PAUSED,
            ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT,
        )
    except Exception:
        logger.debug("Failed to update chat presence", exc_info=True)


async def send_text(client: NewAClient, event: MessageEv, text: str) -> None:
    if len(text) <= MESSAGE_CHUNK_LIMIT:
        await client.reply_message(text, event)
        return

    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n"):
        candidate = paragraph if not current else f"{current}\n{paragraph}"
        if len(candidate) <= MESSAGE_CHUNK_LIMIT:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(paragraph) > MESSAGE_CHUNK_LIMIT:
            chunks.append(paragraph[:MESSAGE_CHUNK_LIMIT])
            paragraph = paragraph[MESSAGE_CHUNK_LIMIT:]
        current = paragraph
    if current:
        chunks.append(current)
    if not chunks:
        chunks = [text[:MESSAGE_CHUNK_LIMIT]]

    await client.reply_message(chunks[0], event)
    chat = event.Info.MessageSource.Chat
    for chunk in chunks[1:]:
        await client.send_message(chat, chunk)


async def process_message(  # noqa: C901, PLR0911, PLR0912, PLR0915
    client: NewAClient,
    event: MessageEv,
    **kwargs: Unpack[ProcessMessageOptions],
) -> None:
    config = kwargs["config"]
    chat_locks = kwargs["chat_locks"]
    source = event.Info.MessageSource
    message = event.Message
    chat_jid = Jid2String(source.Chat)
    chat_key = normalize_chat(chat_jid)
    sender_jid = Jid2String(source.Sender)
    message_id = event.Info.ID

    if source.IsFromMe or (source.IsGroup and not config.allow_groups):
        return

    source_ids = source_chat_ids(source)
    if not is_allowed_chat(config, source_ids):
        logger.info(
            "Ignoring message from %s in %s because it is not allowlisted. Seen IDs: %s",
            sender_jid,
            chat_jid,
            ", ".join(sorted(source_ids)) or "<none>",
        )
        return

    user_text = message.conversation
    if not user_text and message.HasField("extendedTextMessage"):
        user_text = message.extendedTextMessage.text
    user_text = user_text.strip()

    audio = audio_message(event)
    if not user_text and audio is None:
        return

    async with chat_locks[chat_jid]:
        session = get_session(chat_key=chat_key)
        messages_json = get_messages(session)
        if message_id in messages_json["message_ids"]:
            logger.info("Skipping duplicate message %s from %s", message_id, chat_jid)
            return
        messages_json["message_ids"].append(message_id)
        set_messages(session, messages_json)

        logger.info(
            "Received message from %s in %s: %s",
            sender_jid,
            chat_jid,
            user_text or f"<voice note {int(getattr(audio, 'seconds', 0) or 0)}s>",
        )
        if user_text == "/help":
            await client.reply_message(HELP_TEXT, event)
            return
        if user_text == "/reset":
            reset_session = get_session(chat_key=chat_key, session_id=str(uuid4()))
            reset_messages_json = get_messages(reset_session)
            reset_messages_json["message_ids"] = list(messages_json["message_ids"])
            set_messages(reset_session, reset_messages_json)
            await client.reply_message("Memory cleared for this chat.", event)
            return

        typing_stop = asyncio.Event()
        typing_task = asyncio.create_task(
            keep_chat_typing(client, source.Chat, typing_stop)
        )
        try:
            prompt = user_text or await audio_prompt(
                client,
                event,
                openai_api_key=config.openai_api_key,
                transcription_prompt=config.transcription_prompt,
                model=config.openai_transcription_model,
                normalization_model=config.openai_model,
            )
            answer_json = await get_answer(session=session, question=prompt)
            if answer := latest_assistant_text(answer_json):
                await send_text(client, event, answer)
        except AudioError as exc:
            logger.info("Failed to transcribe audio %s: %s", message_id, exc)
            await client.reply_message(str(exc), event)
        except Exception as exc:
            logger.exception("Failed to handle message %s", message_id)
            await client.reply_message(f"Sorry, that failed: {exc}", event)
        finally:
            typing_stop.set()
            await typing_task


def latest_assistant_text(messages_json: MessagesJson | dict[str, Any]) -> str:
    messages = messages_json.get("messages")
    if not isinstance(messages, list):
        return ""

    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if message.get("type") != "message" or message.get("role") != "assistant":
            continue

        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            text = "".join(
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "output_text"
            ).strip()
            if text:
                return text

    return ""
