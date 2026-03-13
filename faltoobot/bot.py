from __future__ import annotations

import asyncio
import logging
import signal
from collections import defaultdict
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import aiosqlite
from neonize.aioze.client import NewAClient
from neonize.aioze.events import ConnectedEv, MessageEv, PairStatusEv
from neonize.utils.jid import Jid2String
from openai import AsyncOpenAI

from faltoobot.config import Config, build_config, normalize_chat
from faltoobot.store import add_turn, open_db, recent_turns, reserve_message, reset_chat

logger = logging.getLogger("faltoobot")


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


def is_allowed_chat(config: Config, chat_jid: str, sender_jid: str) -> bool:
    if not config.allowed_chats:
        return True
    return (
        normalize_chat(chat_jid) in config.allowed_chats
        or normalize_chat(sender_jid) in config.allowed_chats
    )


def should_skip(event: MessageEv, config: Config) -> bool:
    source = event.Info.MessageSource
    if source.IsFromMe:
        return True
    if source.IsGroup and not config.allow_groups:
        return True
    return False


def help_text(config: Config) -> str:
    prefix = config.trigger_prefix or "<empty>"
    return (
        "Faltoobot is online.\n\n"
        f"• {prefix} <prompt> — ask the model\n"
        "• !reset — clear this chat's memory\n"
        "• !help — show this help"
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


async def ask_llm(
    openai_client: AsyncOpenAI, config: Config, db: aiosqlite.Connection, chat_jid: str
) -> str:
    history = await recent_turns(db, chat_jid, config.max_history_messages)
    transcript = "\n".join(f"{row['role']}: {row['content']}" for row in history)
    response = await openai_client.responses.create(
        model=config.openai_model,
        instructions=config.system_prompt,
        input=transcript,
        max_output_tokens=config.max_output_tokens,
    )
    text = (response.output_text or "").strip()
    return text or "I couldn't generate a reply just now."


async def send_text(client: NewAClient, event: MessageEv, text: str, limit: int) -> None:
    chunks = split_message(text, min(limit, 3500))
    if not chunks:
        return
    await client.reply_message(chunks[0], event)
    chat = event.Info.MessageSource.Chat
    for chunk in chunks[1:]:
        await client.send_message(chat, chunk)


async def handle_reset(client: NewAClient, event: MessageEv, db: aiosqlite.Connection) -> None:
    chat_jid = Jid2String(event.Info.MessageSource.Chat)
    await reset_chat(db, chat_jid)
    await client.reply_message("Memory cleared for this chat.", event)


async def handle_prompt(
    client: NewAClient,
    event: MessageEv,
    config: Config,
    db: aiosqlite.Connection,
    openai_client: AsyncOpenAI,
) -> None:
    chat_jid = Jid2String(event.Info.MessageSource.Chat)
    text = message_text(event)
    prompt = text[len(config.trigger_prefix) :].strip() if config.trigger_prefix else text
    if not prompt:
        await client.reply_message(help_text(config), event)
        return
    await add_turn(db, chat_jid, "user", prompt)
    answer = await ask_llm(openai_client, config, db, chat_jid)
    await add_turn(db, chat_jid, "assistant", answer)
    await send_text(client, event, answer, config.max_output_chars)


async def process_message(
    client: NewAClient,
    event: MessageEv,
    config: Config,
    db: aiosqlite.Connection,
    openai_client: AsyncOpenAI,
    chat_locks: dict[str, asyncio.Lock],
) -> None:
    source = event.Info.MessageSource
    chat_jid = Jid2String(source.Chat)
    sender_jid = Jid2String(source.Sender)
    if should_skip(event, config):
        return
    if not is_allowed_chat(config, chat_jid, sender_jid):
        logger.info(
            "Ignoring message from %s in %s because it is not allowlisted", sender_jid, chat_jid
        )
        return
    text = message_text(event)
    if not text:
        return
    if not await reserve_message(db, chat_jid, event.Info.ID):
        logger.info("Skipping duplicate message %s from %s", event.Info.ID, chat_jid)
        return
    async with chat_locks[chat_jid]:
        logger.info("Received message from %s in %s: %s", sender_jid, chat_jid, text)
        if text == "!help":
            await client.reply_message(help_text(config), event)
            return
        if text == "!reset":
            await handle_reset(client, event, db)
            return
        if config.trigger_prefix and not text.startswith(config.trigger_prefix):
            return
        try:
            await handle_prompt(client, event, config, db, openai_client)
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
    await client.stop()


def install_signal_handlers(stop: Callable[[], Awaitable[None]]) -> None:
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
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
    db = await open_db(str(config.state_db))
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
            process_message(current_client, event, config, db, openai_client, chat_locks)
        )
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    if not config.openai_api_key:
        raise RuntimeError(f"openai.api_key is missing. Add it to {config.config_file}")

    logger.info("Using session DB: %s", config.session_db)
    logger.info("Using state DB: %s", config.state_db)
    await client.connect()
    await client.idle()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    await db.close()
    await openai_client.close()
