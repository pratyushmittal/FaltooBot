import asyncio
import logging
import signal
from collections import defaultdict
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from neonize.aioze.client import NewAClient
from neonize.aioze.events import ConnectedEv, MessageEv, PairStatusEv
from neonize.utils.jid import Jid2String
from openai import AsyncOpenAI

from faltoobot.agent import reply
from faltoobot.config import Config, build_config, normalize_chat
from faltoobot.store import (
    Session,
    add_turn,
    reserve_message,
    reset_session,
    session_items,
    whatsapp_session,
)

logger = logging.getLogger("faltoobot")
AUTH_STOP_DELAY = 0.5


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
    text = message.conversation or message.extendedTextMessage.text
    return text.strip()


def source_chat_ids(source: Any) -> set[str]:
    ids = {
        normalize_chat(Jid2String(jid))
        for jid in (source.Chat, source.Sender, source.SenderAlt, source.RecipientAlt)
    }
    return {jid for jid in ids if jid}



def is_allowed_chat(config: Config, source: Any) -> bool:
    if not config.allowed_chats:
        return True
    return not source_chat_ids(source).isdisjoint(config.allowed_chats)


def should_skip(event: MessageEv, config: Config) -> bool:
    source = event.Info.MessageSource
    if source.IsFromMe:
        return True
    if source.IsGroup and not config.allow_groups:
        return True
    return False


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


async def send_text(client: NewAClient, event: MessageEv, text: str) -> None:
    chunks = split_message(text, 3500)
    if not chunks:
        return
    await client.reply_message(chunks[0], event)
    chat = event.Info.MessageSource.Chat
    for chunk in chunks[1:]:
        await client.send_message(chat, chunk)


async def handle_reset(client: NewAClient, event: MessageEv, session: Session) -> Session:
    reset = reset_session(session)
    await client.reply_message("Memory cleared for this chat.", event)
    return reset


async def handle_prompt(
    client: NewAClient,
    event: MessageEv,
    config: Config,
    session: Session,
    openai_client: AsyncOpenAI,
) -> Session:
    prompt = message_text(event)
    if not prompt:
        await client.reply_message(help_text(config), event)
        return session
    session = add_turn(session, "user", prompt)
    result = await reply(
        openai_client,
        config,
        session,
        session_items(session),
    )
    answer = result["text"]
    session = add_turn(
        session,
        "assistant",
        answer,
        items=result["output_items"],
        instructions=result["instructions"],
    )
    await send_text(client, event, answer)
    return session


async def process_message(
    client: NewAClient,
    event: MessageEv,
    config: Config,
    openai_client: AsyncOpenAI,
    chat_locks: dict[str, asyncio.Lock],
    session_index_lock: asyncio.Lock,
) -> None:
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
    if not text:
        return
    async with chat_locks[chat_jid]:
        async with session_index_lock:
            session = whatsapp_session(config.sessions_dir, chat_jid)
        session, is_new = reserve_message(session, event.Info.ID)
        if not is_new:
            logger.info("Skipping duplicate message %s from %s", event.Info.ID, chat_jid)
            return
        logger.info("Received message from %s in %s: %s", sender_jid, chat_jid, text)
        if text == "/help":
            await client.reply_message(help_text(config), event)
            return
        if text == "/reset":
            await handle_reset(client, event, session)
            return
        try:
            await handle_prompt(client, event, config, session, openai_client)
        except Exception as exc:  # comment: this guard keeps the bot alive if one model call fails.
            logger.exception("Failed to handle message %s", event.Info.ID)
            await client.reply_message(f"Sorry, that failed: {exc}", event)


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
    session_index_lock = asyncio.Lock()
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
                config,
                openai_client,
                chat_locks,
                session_index_lock,
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
