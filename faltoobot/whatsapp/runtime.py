import asyncio
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any, TypedDict, cast
from uuid import uuid4

from neonize.aioze.client import NewAClient
from neonize.aioze.events import MessageEv
from neonize.proto import Neonize_pb2
from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import (
    ContextInfo,
    Message,
    MessageAssociation,
)
from neonize.utils.enum import ChatPresence, ChatPresenceMedia
from neonize.utils.jid import Jid2String

from faltoobot.config import Config, config_status_text, normalize_chat
from faltoobot.prompts.transcription import PROMPT as TRANSCRIPTION_PROMPT
from faltoobot.sessions import (
    MessagesJson,
    get_answer,
    get_last_usage,
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
MEDIA_MARKDOWN = re.compile(r"^\s*!\[(?P<caption>[^\]]*)\]\((?P<path>[^)]+)\)\s*$")
BOT_IDENTITY_CACHE: dict[int, set[str]] = {}
SLASH_COMMANDS = {"/help", "/status", "/reset"}


HELP_TEXT = (
    "Faltoobot is online.\n\n"
    "• Send any message to ask the model\n"
    "• /reset — clear this chat's memory\n"
    "• /status — show bot status\n"
    "• /help — show this help"
)


class PendingAlbum(TypedDict):
    """Buffered state for a WhatsApp media album until all its images arrive."""

    expected_images: int
    message_ids: list[str]
    attachments: list[Path]
    prompt: str
    quoted_message_text: str
    reply_event: MessageEv
    sender_name: str | None


def source_chat_ids(source: Any) -> set[str]:
    source_ids = {
        normalize_chat(Jid2String(jid))
        for jid in (source.Chat, source.Sender, source.SenderAlt, source.RecipientAlt)
    }
    return {source_id for source_id in source_ids if source_id}


def _matches_allowed_chats(allowed_chats: set[str], source_ids: set[str]) -> bool:
    if not allowed_chats:
        return True
    if not source_ids.isdisjoint(allowed_chats):
        return True

    for allowed_chat in allowed_chats:
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


class OutgoingMedia(TypedDict):
    path: Path
    caption: str
    is_image: bool


def _outgoing_media(text: str, workspace: Path) -> tuple[str, list[OutgoingMedia]]:
    medias: list[OutgoingMedia] = []
    lines: list[str] = []

    for line in text.splitlines():
        match = MEDIA_MARKDOWN.match(line)
        if match is None:
            lines.append(line)
            continue
        raw_path = match.group("path").strip()
        path = Path(raw_path).expanduser()
        resolved = path if path.is_absolute() else workspace / path
        resolved = resolved.resolve()
        if not resolved.is_file():
            lines.append(line)
            continue
        mime_type = mimetypes.guess_type(resolved.name)[0] or ""
        medias.append(
            {
                "path": resolved,
                "caption": match.group("caption").strip(),
                "is_image": mime_type.startswith("image/"),
            }
        )

    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned, medias


async def _send_media(
    client: NewAClient,
    *,
    chat: Neonize_pb2.JID,
    media: OutgoingMedia,
    event: MessageEv | None = None,
) -> None:
    quoted = event if event is not None else None
    if media["is_image"]:
        await client.send_image(
            chat,
            str(media["path"]),
            caption=media["caption"] or None,
            quoted=quoted,
        )
        return
    await client.send_document(
        chat,
        str(media["path"]),
        caption=media["caption"] or None,
        filename=media["path"].name,
        mimetype=mimetypes.guess_type(media["path"].name)[0] or None,
        quoted=quoted,
    )


async def send_text(  # noqa: C901, PLR0912
    client: NewAClient,
    *,
    chat: Neonize_pb2.JID,
    text: str,
    event: MessageEv | None = None,
    workspace: Path,
) -> None:
    text, medias = _outgoing_media(text, workspace)

    if text and len(text) <= MESSAGE_CHUNK_LIMIT:
        if event is None:
            await client.send_message(chat, text)
        else:
            await client.reply_message(text, event)
    elif text:
        chunks: list[str] = []
        current = ""
        for paragraph in text.splitlines():
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

        if event is not None:
            await client.reply_message(chunks.pop(0), event)
        for chunk in chunks:
            await client.send_message(chat, chunk)

    # comment: only the first media should quote-reply to the incoming message when there
    # is no text body, because WhatsApp treats later media as follow-ups in the same reply.
    for index, media in enumerate(medias):
        await _send_media(
            client,
            chat=chat,
            media=media,
            event=event if not text and index == 0 else None,
        )


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


class Turn(TypedDict):
    # comment: event is kept for normal WhatsApp messages because quote-reply uses it.
    event: MessageEv | None
    # comment: chat is used for direct typing updates and direct replies in this chat.
    chat: Neonize_pb2.JID
    message_ids: list[str]
    prompt: str
    quoted_message_text: str
    attachments: list[Path]
    audio: Any


async def _handle_slash_command(
    client: NewAClient,
    *,
    session: tuple[str, str],
    config: Config,
    turn: Turn,
    messages_json: MessagesJson,
) -> None:
    event = cast(MessageEv, turn["event"])
    prompt = turn["prompt"]
    if prompt == "/help":
        await client.reply_message(HELP_TEXT, event)
    elif prompt == "/status":
        await client.reply_message(
            config_status_text(config, get_last_usage(session)), event
        )
    elif prompt == "/reset":
        reset_session = get_session(chat_key=session[0], session_id=str(uuid4()))
        reset_messages_json = get_messages(reset_session)
        reset_messages_json["message_ids"] = list(messages_json["message_ids"])
        set_messages(reset_session, reset_messages_json)
        await client.reply_message("Memory cleared for this chat.", event)


async def process_turn_locked(
    client: NewAClient,
    session: tuple[str, str],
    *,
    config: Config,
    turn: "Turn",
) -> None:
    """Process one already-stored turn while the per-chat lock is held."""
    event = turn["event"]
    chat = turn["chat"]
    prompt = turn["prompt"]
    attachments = turn["attachments"]
    messages_json = get_messages(session)
    if event is not None and not attachments and prompt in SLASH_COMMANDS:
        await _handle_slash_command(
            client,
            session=session,
            config=config,
            turn=turn,
            messages_json=messages_json,
        )
        return
    workspace = Path(messages_json["workspace"])

    typing_stop = asyncio.Event()
    typing_task = asyncio.create_task(keep_chat_typing(client, chat, typing_stop))
    try:
        answer = await get_answer(session)
        if answer and answer.strip() != "[noreply]":
            await send_text(
                client, chat=chat, text=answer, event=event, workspace=workspace
            )
    except asyncio.CancelledError:
        await send_text(
            client,
            chat=chat,
            text="interrupted by user",
            event=event,
            workspace=workspace,
        )
        raise
    except Exception as exc:
        logger.exception(
            "Failed to handle message %s",
            event.Info.ID if event is not None else "<notify>",
        )
        await send_text(
            client,
            chat=chat,
            text=f"Sorry, that failed: {exc}",
            event=event,
            workspace=workspace,
        )
    finally:
        typing_stop.set()
        await typing_task


def _message_text(message: Any) -> str:
    text = message.conversation
    if not text and message.HasField("extendedTextMessage"):
        text = message.extendedTextMessage.text
    if not text and message.HasField("imageMessage"):
        text = message.imageMessage.caption
    return text.strip()


def _message_context_info(message: Message) -> ContextInfo | None:
    for field_name in (
        "extendedTextMessage",
        "imageMessage",
        "audioMessage",
        "albumMessage",
    ):
        if not message.HasField(field_name):
            continue
        field = getattr(message, field_name)
        if field.HasField("contextInfo"):
            return field.contextInfo
    return None


def _quoted_message_text(message: Message) -> str:
    """Return the plain-text body of the quoted WhatsApp message, if any."""
    context_info = _message_context_info(message)
    # comment: most WhatsApp messages are not replies, so there is no quoted message to
    # thread into the model prompt.
    if context_info is None or not context_info.HasField("quotedMessage"):
        return ""
    # comment: quoted messages can be text, captions, or transcripts; `_message_text`
    # already normalizes those shapes into one plain-text string.
    return _message_text(context_info.quotedMessage)


def _mentioned_chat_ids(message: Message) -> set[str]:
    context_info = _message_context_info(message)
    if context_info is None:
        return set()
    mentioned = {normalize_chat(str(chat)) for chat in context_info.mentionedJID}
    return {chat for chat in mentioned if chat}


def _prompt_with_sender(prompt: str, sender_name: str | None) -> str:
    text = prompt.strip()
    if text in SLASH_COMMANDS or not sender_name:
        return text
    speaker = " ".join(sender_name.split()).strip()
    if not speaker:
        return text
    prefix = f"[from {speaker}]"
    if not text:
        return prefix
    return f"{prefix}\n{text}" if "\n" in text else f"{prefix} {text}"


def _quoted_participant_ids(message: Message) -> set[str]:
    """Return normalized participant IDs referenced by the quoted message context."""
    context_info = _message_context_info(message)
    if context_info is None:
        return set()
    quoted_ids = {
        normalize_chat(str(context_info.participant)),
        normalize_chat(str(context_info.remoteJID)),
    }
    return {chat for chat in quoted_ids if chat}


def _sender_name(event: MessageEv) -> str | None:
    pushname = " ".join(
        str(
            getattr(event.Info, "PushName", "")
            or getattr(event.Info, "Pushname", "")
            or ""
        ).split()
    ).strip()
    if pushname:
        return pushname
    source = event.Info.MessageSource
    for jid in (source.SenderAlt, source.Sender):
        user = str(getattr(jid, "User", "") or "").strip()
        if user:
            return user.split(":", 1)[0]
    return None


async def _bot_identity_ids(client: NewAClient) -> set[str]:
    """Return the normalized WhatsApp IDs that identify the connected bot account."""
    cache_key = id(client)
    cached = BOT_IDENTITY_CACHE.get(cache_key)
    if cached is not None:
        return cached
    device = await client.get_me()
    identity_ids = {
        normalize_chat(Jid2String(jid))
        for jid in (device.JID, device.LID)
        if getattr(jid, "User", "") or getattr(jid, "Server", "")
    }
    normalized = {identity_id for identity_id in identity_ids if identity_id}
    BOT_IDENTITY_CACHE[cache_key] = normalized
    return normalized


async def is_unmentioned_group_message(
    client: NewAClient,
    event: MessageEv | None,
) -> bool:
    """Return whether the event is a group message that neither mentions nor quotes the bot."""
    if event is None or not event.Info.MessageSource.IsGroup:
        return False
    addressed_ids = _mentioned_chat_ids(event.Message) | _quoted_participant_ids(
        event.Message
    )
    if not addressed_ids:
        return True
    bot_ids = await _bot_identity_ids(client)
    return addressed_ids.isdisjoint(bot_ids)


def _quoted_reply_text(text: str, *, max_chars: int = 500) -> str:
    snippet = text.strip()
    if len(snippet) > max_chars:
        snippet = f"{snippet[: max_chars - 3].rstrip()}..."
    return "\n".join(">" if not line else f"> {line}" for line in snippet.splitlines())


def _prompt_with_reply_context(prompt: str, quoted_message_text: str) -> str:
    if not quoted_message_text:
        return prompt
    quoted = _quoted_reply_text(quoted_message_text)
    if not prompt:
        return (
            "The user sent this as a reply to an earlier message.\n\n"
            f"Earlier message:\n{quoted}"
        )
    return (
        "The user is replying to an earlier message.\n\n"
        f"Earlier message:\n{quoted}\n\n"
        f"User reply:\n{prompt}"
    )


def _album_id(message: Any, message_id: str) -> tuple[int, str | None]:
    expected_images = (
        int(message.albumMessage.expectedImageCount or 0)
        if message.HasField("albumMessage")
        else 0
    )
    if expected_images:
        return expected_images, message_id
    if (
        message.HasField("messageContextInfo")
        and message.messageContextInfo.HasField("messageAssociation")
        and message.messageContextInfo.messageAssociation.associationType
        == MessageAssociation.MEDIA_ALBUM
    ):
        return (
            expected_images,
            str(
                message.messageContextInfo.messageAssociation.parentMessageKey.ID or ""
            ).strip()
            or None,
        )
    return expected_images, None


def _turn_from_pending_album(pending_album: PendingAlbum) -> Turn:
    return {
        "event": pending_album["reply_event"],
        "chat": pending_album["reply_event"].Info.MessageSource.Chat,
        "message_ids": pending_album["message_ids"],
        "prompt": _prompt_with_sender(
            _prompt_with_reply_context(
                pending_album["prompt"], pending_album["quoted_message_text"]
            ),
            pending_album["sender_name"],
        ),
        "quoted_message_text": "",
        "attachments": pending_album["attachments"],
        "audio": None,
    }


async def _handle_pending_album(  # noqa: PLR0913
    client: NewAClient,
    *,
    pending_albums: dict[str, PendingAlbum],
    current_album_id: str | None,
    message_id: str,
    chat_jid: str,
    image_message: bool,
    user_text: str,
    quoted_message_text: str,
    sender_name: str | None,
    message: Any,
    workspace: Path,
) -> Turn | None:
    if not current_album_id:
        return None
    pending_album = pending_albums.get(current_album_id)
    if pending_album is None:
        return None
    if message_id in pending_album["message_ids"]:
        logger.info("Skipping duplicate message %s from %s", message_id, chat_jid)
        return None
    if not image_message:
        pending_albums.pop(current_album_id, None)
        return (
            _turn_from_pending_album(pending_album)
            if pending_album["attachments"]
            else None
        )

    # comment: absorb the next image into the buffered album turn.
    pending_album["message_ids"].append(message_id)
    pending_album["attachments"].append(
        await save_image_attachment(
            client,
            message,
            workspace=workspace,
            message_id=message_id,
        )
    )
    # comment: merge any caption text into the album prompt as images arrive.
    if user_text and user_text != pending_album["prompt"]:
        pending_album["prompt"] = (
            user_text
            if not pending_album["prompt"]
            else f"{pending_album['prompt']}\n{user_text}"
        )
    # comment: remember the first quoted-message context so the final turn keeps it.
    if quoted_message_text and not pending_album["quoted_message_text"]:
        pending_album["quoted_message_text"] = quoted_message_text
    if sender_name and not pending_album["sender_name"]:
        pending_album["sender_name"] = sender_name
    # comment: keep buffering until WhatsApp says the album is complete.
    if len(pending_album["attachments"]) < pending_album["expected_images"]:
        return None
    pending_albums.pop(current_album_id, None)
    return _turn_from_pending_album(pending_album)


def _is_group_allowed(
    event: MessageEv,
    *,
    config: Config,
    chat_jid: str,
    sender_jid: str,
) -> bool:
    source_ids = source_chat_ids(event.Info.MessageSource)
    if not config.allow_group_chats or not _matches_allowed_chats(
        config.allow_group_chats, source_ids
    ):
        logger.info(
            "Ignoring group message from %s in %s because it is not group-allowlisted. Seen IDs: %s",
            sender_jid,
            chat_jid,
            ", ".join(sorted(source_ids)) or "<none>",
        )
        return False
    return True


async def _should_store_event(
    event: MessageEv,
    *,
    config: Config,
    chat_jid: str,
    sender_jid: str,
) -> bool:
    source = event.Info.MessageSource
    if source.IsFromMe:
        return False

    if source.IsGroup:
        return _is_group_allowed(
            event,
            config=config,
            chat_jid=chat_jid,
            sender_jid=sender_jid,
        )

    source_ids = source_chat_ids(source)
    allowed = _matches_allowed_chats(config.allowed_chats, source_ids)
    if not allowed:
        logger.info(
            "Ignoring message from %s in %s because it is not allowlisted. Seen IDs: %s",
            sender_jid,
            chat_jid,
            ", ".join(sorted(source_ids)) or "<none>",
        )
    return allowed


async def get_turn_locked(  # noqa: C901, PLR0911
    client: NewAClient,
    event: MessageEv,
    *,
    config: Config,
    session: tuple[str, str],
    pending_albums: dict[str, PendingAlbum] | None = None,
) -> Turn | None:
    """Return a normalized turn for one event while the caller holds the chat lock."""
    pending_albums = {} if pending_albums is None else pending_albums
    source = event.Info.MessageSource
    chat_jid = Jid2String(source.Chat)
    sender_jid = Jid2String(source.Sender)

    if not await _should_store_event(
        event,
        config=config,
        chat_jid=chat_jid,
        sender_jid=sender_jid,
    ):
        return None

    sender_name = _sender_name(event) if source.IsGroup else None

    workspace = Path(get_messages(session)["workspace"])
    message = event.Message
    message_id = event.Info.ID
    user_text = _message_text(message)
    quoted_message_text = _quoted_message_text(message)
    audio = audio_message(event)
    image_message = message.HasField("imageMessage")
    expected_album_images, current_album_id = _album_id(message, message_id)
    if not user_text and audio is None and not image_message and not current_album_id:
        return None

    # comment: WhatsApp sends media albums as separate events. We buffer them by album
    # id until all expected images arrive, then turn the finished album into one prompt.
    pending_album_turn = await _handle_pending_album(
        client,
        pending_albums=pending_albums,
        current_album_id=current_album_id,
        message_id=message_id,
        chat_jid=chat_jid,
        image_message=image_message,
        user_text=user_text,
        quoted_message_text=quoted_message_text,
        sender_name=sender_name,
        message=message,
        workspace=workspace,
    )
    if pending_album_turn is not None:
        summary = pending_album_turn["prompt"]
        if not summary:
            count = len(pending_album_turn["attachments"])
            summary = "<image>" if count == 1 else f"<{count} images>"
        logger.info(
            "Received message from %s in %s: %s",
            sender_jid,
            chat_jid,
            summary,
        )
        return pending_album_turn
    if current_album_id and current_album_id in pending_albums:
        return None
    if expected_album_images and current_album_id:
        pending_albums[current_album_id] = {
            "expected_images": expected_album_images,
            "message_ids": [message_id],
            "attachments": [],
            "prompt": "",
            "quoted_message_text": quoted_message_text,
            "reply_event": event,
            "sender_name": sender_name,
        }
        return None
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
        else []
    )
    summary = user_text
    if not summary:
        if attachments:
            summary = (
                "<image>" if len(attachments) == 1 else f"<{len(attachments)} images>"
            )
        else:
            summary = f"<voice note {int(getattr(audio, 'seconds', 0) or 0)}s>"
    if not user_text and audio is not None:
        try:
            user_text = await audio_prompt(
                client,
                event,
                openai_api_key=config.openai_api_key,
                transcription_prompt=TRANSCRIPTION_PROMPT,
                model=config.openai_transcription_model,
            )
        except AudioError as exc:
            logger.info("Failed to transcribe audio %s: %s", event.Info.ID, exc)
            await send_text(
                client,
                chat=event.Info.MessageSource.Chat,
                text=str(exc),
                event=event,
                workspace=workspace,
            )
            return None
    logger.info(
        "Received message from %s in %s: %s",
        sender_jid,
        chat_jid,
        summary,
    )
    return {
        "event": event,
        "chat": event.Info.MessageSource.Chat,
        "message_ids": [message_id],
        "prompt": _prompt_with_sender(
            _prompt_with_reply_context(user_text, quoted_message_text),
            sender_name,
        ),
        "quoted_message_text": "",
        "attachments": attachments,
        "audio": None,
    }
