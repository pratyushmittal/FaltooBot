import asyncio
import json
import logging
import signal
from collections import defaultdict
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, TypedDict, Unpack

from neonize.aioze.client import NewAClient
from neonize.aioze.events import ConnectedEv, MessageEv, PairStatusEv
from neonize.utils.enum import ChatPresence, ChatPresenceMedia
from neonize.utils.jid import Jid2String
from openai import AsyncOpenAI

from faltoobot.audio import AudioError, audio_message, audio_prompt
from faltoobot.config import Config, build_config, normalize_chat
from faltoobot.sessions import (
    MessagesJson,
    get_answer,
    get_messages,
    get_session_id,
    set_messages,
)

logger = logging.getLogger("faltoobot")
AUTH_STOP_DELAY = 0.5
TYPING_REFRESH_SECONDS = 4.0
MIN_ALLOWLIST_DIGITS = 8
CHAT_SESSIONS_FILE = "whatsapp-sessions.json"


def configure_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )


def message_text(event: MessageEv) -> str:
    message = event.Message
    text = message.conversation
    if not text and message.HasField("extendedTextMessage"):
        text = message.extendedTextMessage.text
    return text.strip()


def source_chat_ids(source: Any) -> set[str]:
    ids = {
        normalize_chat(Jid2String(jid))
        for jid in (source.Chat, source.Sender, source.SenderAlt, source.RecipientAlt)
    }
    return {jid for jid in ids if jid}


def phone_digits(value: str) -> str:
    if not value.endswith("@s.whatsapp.net"):
        return ""
    return value.split("@", 1)[0]


def phone_id_matches(left: str, right: str) -> bool:
    left_digits = phone_digits(left)
    right_digits = phone_digits(right)
    if (
        min(len(left_digits), len(right_digits)) < MIN_ALLOWLIST_DIGITS
    ):  # comment: short suffixes are too loose for allowlists.
        return False
    return left_digits.endswith(right_digits) or right_digits.endswith(left_digits)


def is_allowed_chat(config: Config, source: Any) -> bool:
    if not config.allowed_chats:
        return True
    ids = source_chat_ids(source)
    if not ids.isdisjoint(config.allowed_chats):
        return True
    return any(
        phone_id_matches(allowed, seen)
        for allowed in config.allowed_chats
        for seen in ids
    )


def should_skip(event: MessageEv, config: Config) -> bool:
    source = event.Info.MessageSource
    if source.IsFromMe:
        return True
    if source.IsGroup and not config.allow_groups:
        return True
    return False


class ProcessMessageOptions(TypedDict):
    config: Config
    openai_client: AsyncOpenAI
    chat_locks: dict[str, asyncio.Lock]


def _chat_sessions_path(config: Config) -> Path:
    return config.root / CHAT_SESSIONS_FILE


def _read_chat_sessions(config: Config) -> dict[str, str]:
    path = _chat_sessions_path(config)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        key: value
        for key, value in payload.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _write_chat_sessions(config: Config, payload: dict[str, str]) -> None:
    path = _chat_sessions_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp")
    temp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temp.replace(path)


def _chat_session_id(config: Config, chat_jid: str) -> str:
    key = normalize_chat(chat_jid)
    payload = _read_chat_sessions(config)
    if key in payload:
        return get_session_id(kind="whatsapp", session_id=payload[key])
    session_id = get_session_id(kind="whatsapp")
    _write_chat_sessions(config, {**payload, key: session_id})
    return session_id


def _replace_chat_session(config: Config, chat_jid: str, message_ids: list[str]) -> str:
    session_id = get_session_id(kind="whatsapp")
    messages_json = get_messages(session_id)
    messages_json["message_ids"] = list(message_ids)
    set_messages(session_id, messages_json)
    payload = _read_chat_sessions(config)
    _write_chat_sessions(config, {**payload, normalize_chat(chat_jid): session_id})
    return session_id


def help_text(config: Config) -> str:
    return (
        "Faltoobot is online.\n\n"
        "• Send any message to ask the model\n"
        "• /reset — clear this chat's memory\n"
        "• /help — show this help"
    )


def split_message(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n"):
        candidate = paragraph if not current else f"{current}\n{paragraph}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(paragraph) > limit:
            chunks.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        current = paragraph
    if current:
        chunks.append(current)
    return chunks or [text[:limit]]


async def send_chat_state(client: NewAClient, chat: Any, state: ChatPresence) -> None:
    try:
        await client.send_chat_presence(
            chat,
            state,
            ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT,
        )
    except Exception:
        logger.debug("Failed to update chat presence", exc_info=True)


async def keep_chat_typing(client: NewAClient, chat: Any, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await send_chat_state(client, chat, ChatPresence.CHAT_PRESENCE_COMPOSING)
        try:
            await asyncio.wait_for(stop.wait(), timeout=TYPING_REFRESH_SECONDS)
        except TimeoutError:
            continue
    await send_chat_state(client, chat, ChatPresence.CHAT_PRESENCE_PAUSED)


async def send_text(client: NewAClient, event: MessageEv, text: str) -> None:
    chunks = split_message(text, 3500)
    if not chunks:
        return
    await client.reply_message(chunks[0], event)
    chat = event.Info.MessageSource.Chat
    for chunk in chunks[1:]:
        await client.send_message(chat, chunk)


def _message_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    return "".join(
        str(part.get("text") or "")
        for part in content
        if isinstance(part, dict) and part.get("type") == "output_text"
    ).strip()


def _latest_assistant_text(messages_json: MessagesJson | dict[str, Any]) -> str:
    raw_messages = messages_json.get("messages", [])
    if not isinstance(raw_messages, list):
        return ""
    for raw_item in reversed(raw_messages):
        if not isinstance(raw_item, dict):
            continue
        item: dict[str, Any] = raw_item
        if item.get("type") != "message":
            continue
        if item.get("role") != "assistant":
            continue
        if text := _message_text_content(item.get("content")):
            return text
    return ""


def _reserve_message_id(session_id: str, message_id: str) -> bool:
    messages_json = get_messages(session_id)
    if message_id in messages_json["message_ids"]:
        return False
    messages_json["message_ids"].append(message_id)
    set_messages(session_id, messages_json)
    return True


async def handle_reset(
    client: NewAClient,
    event: MessageEv,
    config: Config,
    chat_jid: str,
    session_id: str,
) -> None:
    messages_json = get_messages(session_id)
    _replace_chat_session(config, chat_jid, messages_json["message_ids"])
    await client.reply_message("Memory cleared for this chat.", event)


async def handle_prompt(
    client: NewAClient,
    event: MessageEv,
    config: Config,
    session_id: str,
    prompt: str,
) -> None:
    if not prompt:
        await client.reply_message(help_text(config), event)
        return
    messages_json = await get_answer(session_id=session_id, question=prompt)
    answer = _latest_assistant_text(messages_json)
    if answer:
        await send_text(client, event, answer)


async def process_message(
    client: NewAClient,
    event: MessageEv,
    **kwargs: Unpack[ProcessMessageOptions],
) -> None:
    config = kwargs["config"]
    openai_client = kwargs["openai_client"]
    chat_locks = kwargs["chat_locks"]
    source = event.Info.MessageSource
    chat_jid = Jid2String(source.Chat)
    sender_jid = Jid2String(source.Sender)
    if should_skip(event, config):
        return
    candidate_ids = source_chat_ids(source)
    if not is_allowed_chat(config, source):
        logger.info(
            "Ignoring message from %s in %s because it is not allowlisted. Seen IDs: %s",
            sender_jid,
            chat_jid,
            ", ".join(sorted(candidate_ids)) or "<none>",
        )
        return
    text = message_text(event)
    audio = audio_message(event)
    if not text and audio is None:
        return
    async with chat_locks[chat_jid]:
        session_id = _chat_session_id(config, chat_jid)
        if not _reserve_message_id(session_id, event.Info.ID):
            logger.info(
                "Skipping duplicate message %s from %s", event.Info.ID, chat_jid
            )
            return
        logger.info(
            "Received message from %s in %s: %s",
            sender_jid,
            chat_jid,
            text or f"<voice note {int(getattr(audio, 'seconds', 0) or 0)}s>",
        )
        if text == "/help":
            await client.reply_message(help_text(config), event)
            return
        if text == "/reset":
            await handle_reset(client, event, config, chat_jid, session_id)
            return
        typing_stop = asyncio.Event()
        typing_task = asyncio.create_task(
            keep_chat_typing(client, source.Chat, typing_stop)
        )
        try:
            prompt = text or await audio_prompt(
                client,
                event,
                openai_client,
                transcription_prompt=config.transcription_prompt,
                model=config.openai_transcription_model,
                normalization_model=config.openai_model,
            )
            await handle_prompt(client, event, config, session_id, prompt)
        except AudioError as exc:
            logger.info("Failed to transcribe audio %s: %s", event.Info.ID, exc)
            await client.reply_message(str(exc), event)
        except Exception as exc:  # comment: this guard keeps the bot alive if one model call fails.
            logger.exception("Failed to handle message %s", event.Info.ID)
            await client.reply_message(f"Sorry, that failed: {exc}", event)
        finally:
            typing_stop.set()
            await typing_task


async def wait_for_login(client: NewAClient) -> None:
    ready = asyncio.Event()

    @client.event(ConnectedEv)
    async def _on_connected(_: NewAClient, __: ConnectedEv) -> None:
        logger.info("WhatsApp connected")
        ready.set()

    @client.event(PairStatusEv)
    async def _on_pair_status(_: NewAClient, event: PairStatusEv) -> None:
        logger.info("Pair status: %s", event.Status)

    await client.connect()
    await ready.wait()
    logger.info("Auth successful. Session saved.")
    logger.info("Next step: run `faltoobot run`")
    await asyncio.sleep(AUTH_STOP_DELAY)
    await client.stop()


def install_signal_handlers(stop: Callable[[], Coroutine[Any, Any, None]]) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, lambda: asyncio.create_task(stop()))


async def run_auth(config: Config | None = None) -> None:
    config = config or build_config()
    configure_logging(config.log_file)
    client = NewAClient(str(config.session_db))
    logger.info("Starting auth flow. Scan the QR code shown below.")
    await wait_for_login(client)


async def run_bot(config: Config | None = None) -> None:
    config = config or build_config()
    configure_logging(config.log_file)
    openai_client = AsyncOpenAI(api_key=config.openai_api_key)
    client = NewAClient(str(config.session_db))
    chat_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
    tasks: set[asyncio.Task[Any]] = set()

    async def stop() -> None:
        logger.info("Stopping Faltoobot")
        await client.stop()

    install_signal_handlers(stop)

    @client.event(ConnectedEv)
    async def _on_connected(_: NewAClient, __: ConnectedEv) -> None:
        logger.info("Faltoobot connected to WhatsApp")

    @client.event(PairStatusEv)
    async def _on_pair_status(_: NewAClient, event: PairStatusEv) -> None:
        logger.info("Pair status: %s", event.Status)

    @client.event(MessageEv)
    async def _on_message(current_client: NewAClient, event: MessageEv) -> None:
        task = asyncio.create_task(
            process_message(
                current_client,
                event,
                config=config,
                openai_client=openai_client,
                chat_locks=chat_locks,
            )
        )
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    if not config.openai_api_key:
        raise RuntimeError(f"openai.api_key is missing. Add it to {config.config_file}")

    logger.info("Using session DB: %s", config.session_db)
    logger.info("Using sessions dir: %s", config.sessions_dir)
    await client.connect()
    await client.idle()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    await openai_client.close()
