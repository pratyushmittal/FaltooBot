import asyncio
import logging
import mimetypes
from pathlib import Path
from typing import Any, TypedDict, Unpack
from uuid import uuid4

from neonize.aioze.client import NewAClient
from neonize.aioze.events import MessageEv
from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import MessageAssociation
from neonize.utils.enum import ChatPresence, ChatPresenceMedia
from neonize.utils.jid import Jid2String

from faltoobot.config import Config, normalize_chat
from faltoobot.prompts.transcription import PROMPT as TRANSCRIPTION_PROMPT
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
WHATSAPP_MEDIA_DIR = ".whatsapp"
IMAGE_SUFFIXES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}
HELP_TEXT = (
    "Faltoobot is online.\n\n"
    "• Send any message to ask the model\n"
    "• /reset — clear this chat's memory\n"
    "• /help — show this help"
)


class PendingAlbum(TypedDict):
    expected_images: int
    message_ids: list[str]
    attachments: list[Path]
    prompt: str
    reply_event: MessageEv


class ProcessMessageOptions(TypedDict, total=False):
    config: Config
    chat_locks: dict[str, asyncio.Lock]
    pending_albums: dict[str, PendingAlbum]


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


async def save_image_attachment(
    client: NewAClient,
    message: Any,
    *,
    workspace: Path,
    message_id: str,
) -> Path:
    image_bytes = await client.download_any(message)
    if image_bytes is None:
        raise ValueError("WhatsApp image download returned no data")

    suffix = IMAGE_SUFFIXES.get(message.imageMessage.mimetype.lower())
    if not suffix:
        suffix = mimetypes.guess_extension(message.imageMessage.mimetype) or ".jpg"
    if suffix == ".jpe":
        suffix = ".jpg"
    path = workspace / WHATSAPP_MEDIA_DIR / f"{message_id.replace('/', '_')}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(image_bytes)
    return path


async def process_turn_locked(
    client: NewAClient,
    session: tuple[str, str],
    *,
    config: Config,
    turn: dict[str, Any],
) -> None:
    event = turn["event"]
    source = event.Info.MessageSource
    chat_jid = Jid2String(source.Chat)
    sender_jid = Jid2String(source.Sender)
    message_ids = turn["message_ids"]
    prompt = turn["prompt"]
    attachments = turn["attachments"]
    audio = turn["audio"]

    messages_json = get_messages(session)
    fresh_message_ids = [
        item for item in message_ids if item not in messages_json["message_ids"]
    ]
    if not fresh_message_ids:
        logger.info(
            "Skipping duplicate message%s %s from %s",
            "" if len(message_ids) == 1 else "s",
            ", ".join(message_ids),
            chat_jid,
        )
        return
    messages_json["message_ids"].extend(fresh_message_ids)
    set_messages(session, messages_json)

    summary = prompt
    if not summary:
        if attachments:
            summary = (
                "<image>" if len(attachments) == 1 else f"<{len(attachments)} images>"
            )
        else:
            summary = f"<voice note {int(getattr(audio, 'seconds', 0) or 0)}s>"
    logger.info(
        "Received message from %s in %s: %s",
        sender_jid,
        chat_jid,
        summary,
    )
    if not attachments and audio is None and prompt == "/help":
        await client.reply_message(HELP_TEXT, event)
        return
    if not attachments and audio is None and prompt == "/reset":
        reset_session = get_session(chat_key=session[0], session_id=str(uuid4()))
        reset_messages_json = get_messages(reset_session)
        reset_messages_json["message_ids"] = list(messages_json["message_ids"])
        set_messages(reset_session, reset_messages_json)
        await client.reply_message("Memory cleared for this chat.", event)
        return

    typing_stop = asyncio.Event()
    typing_task = asyncio.create_task(
        keep_chat_typing(client, event.Info.MessageSource.Chat, typing_stop)
    )
    try:
        if not prompt and audio is not None:
            prompt = await audio_prompt(
                client,
                event,
                openai_api_key=config.openai_api_key,
                transcription_prompt=TRANSCRIPTION_PROMPT,
                model=config.openai_transcription_model,
                normalization_model=config.openai_model,
            )
        answer_json = await get_answer(
            session=session,
            question=prompt,
            attachments=attachments or None,
        )
        if answer := latest_assistant_text(answer_json):
            await send_text(client, event, answer)
    except AudioError as exc:
        logger.info("Failed to transcribe audio %s: %s", event.Info.ID, exc)
        await client.reply_message(str(exc), event)
    except Exception as exc:
        logger.exception("Failed to handle message %s", event.Info.ID)
        await client.reply_message(f"Sorry, that failed: {exc}", event)
    finally:
        typing_stop.set()
        await typing_task


async def process_message(  # noqa: C901, PLR0911, PLR0912, PLR0915
    client: NewAClient,
    event: MessageEv,
    **kwargs: Unpack[ProcessMessageOptions],
) -> None:
    config = kwargs["config"]
    chat_locks = kwargs["chat_locks"]
    pending_albums = kwargs["pending_albums"] if "pending_albums" in kwargs else {}
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
    if not user_text and message.HasField("imageMessage"):
        user_text = message.imageMessage.caption
    user_text = user_text.strip()

    audio = audio_message(event)
    image_message = message.HasField("imageMessage")
    expected_album_images = (
        int(message.albumMessage.expectedImageCount or 0)
        if message.HasField("albumMessage")
        else 0
    )
    current_album_id: str | None = None
    if expected_album_images:
        current_album_id = message_id
    elif (
        message.HasField("messageContextInfo")
        and message.messageContextInfo.HasField("messageAssociation")
        and message.messageContextInfo.messageAssociation.associationType
        == MessageAssociation.MEDIA_ALBUM
    ):
        current_album_id = (
            str(
                message.messageContextInfo.messageAssociation.parentMessageKey.ID or ""
            ).strip()
            or None
        )
    if not user_text and audio is None and not image_message and not current_album_id:
        return

    async with chat_locks[chat_jid]:
        session = get_session(chat_key=chat_key)
        workspace = Path(get_messages(session)["workspace"])
        if current_album_id and (pending_album := pending_albums.get(current_album_id)):
            if message_id in pending_album["message_ids"]:
                logger.info(
                    "Skipping duplicate message %s from %s", message_id, chat_jid
                )
                return
            if image_message:
                pending_album["message_ids"].append(message_id)
                pending_album["attachments"].append(
                    await save_image_attachment(
                        client,
                        message,
                        workspace=workspace,
                        message_id=message_id,
                    )
                )
                incoming_prompt = user_text.strip()
                if incoming_prompt and incoming_prompt != pending_album["prompt"]:
                    pending_album["prompt"] = (
                        incoming_prompt
                        if not pending_album["prompt"]
                        else f"{pending_album['prompt']}\n{incoming_prompt}"
                    )
                if len(pending_album["attachments"]) < pending_album["expected_images"]:
                    return
                pending_albums.pop(current_album_id, None)
                await process_turn_locked(
                    client,
                    session,
                    config=config,
                    turn={
                        "event": pending_album["reply_event"],
                        "message_ids": pending_album["message_ids"],
                        "prompt": pending_album["prompt"],
                        "attachments": pending_album["attachments"],
                        "audio": None,
                    },
                )
                return
            pending_albums.pop(current_album_id, None)
            if pending_album["attachments"]:
                await process_turn_locked(
                    client,
                    session,
                    config=config,
                    turn={
                        "event": pending_album["reply_event"],
                        "message_ids": pending_album["message_ids"],
                        "prompt": pending_album["prompt"],
                        "attachments": pending_album["attachments"],
                        "audio": None,
                    },
                )

        if expected_album_images and current_album_id:
            pending_albums[current_album_id] = {
                "expected_images": expected_album_images,
                "message_ids": [message_id],
                "attachments": [],
                "prompt": "",
                "reply_event": event,
            }
            return

        attachments = (
            [
                await save_image_attachment(
                    client,
                    message,
                    workspace=workspace,
                    message_id=message_id,
                )
            ]
            if image_message
            else None
        )
        await process_turn_locked(
            client,
            session,
            config=config,
            turn={
                "event": event,
                "message_ids": [message_id],
                "prompt": user_text,
                "attachments": attachments or [],
                "audio": audio,
            },
        )


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
