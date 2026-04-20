import asyncio
import logging
import signal
from collections import defaultdict
from typing import Any

from neonize.aioze.client import NewAClient
from neonize.aioze.events import ConnectedEv, MessageEv, PairStatusEv
from neonize.utils.jid import Jid2String, build_jid

from faltoobot import notify_queue
from faltoobot.config import Config, build_config, normalize_chat
from faltoobot.sessions import append_user_turn, get_session

from . import login, runtime

logger = logging.getLogger("faltoobot")
DEBOUNCE_SECONDS = 3.0

__all__ = ["main"]

config: Config = build_config()
client = NewAClient(str(config.session_db))
tasks: set[asyncio.Task[Any]] = set()
# comment: serialize turns per WhatsApp chat so follow-up messages wait for the current
# turn to finish instead of racing and corrupting shared session history.
chat_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
debounce_timers: dict[str, asyncio.TimerHandle] = {}
pending_albums: dict[str, runtime.PendingAlbum] = {}
notifications_stop = asyncio.Event()


async def on_exit() -> None:
    logger.info("Stopping Faltoobot")
    notifications_stop.set()
    for handle in debounce_timers.values():
        handle.cancel()
    for task in list(tasks):
        task.cancel()
    await client.stop()


async def _start_polling_notifications() -> None:
    while not notifications_stop.is_set():
        for path, notification in notify_queue.claim_notifications(
            lambda item: item["chat_key"].endswith(("@lid", "@s.whatsapp.net", "@g.us"))
        ):
            try:
                chat_key = notification["chat_key"]
                user, server = chat_key.split("@", 1)
                # comment: notify-queue items only store the string chat key. We rebuild the
                # JID object here because typing presence and the final reply both need it.
                chat_jid = build_jid(user, server)
                async with chat_locks[chat_key]:
                    session = get_session(chat_key=chat_key)
                    turn: runtime.Turn = {
                        "event": None,
                        "chat": chat_jid,
                        "message_ids": [notification["id"]],
                        "prompt": notify_queue.format_notification_message(
                            notification
                        ),
                        "quoted_message_text": "",
                        "attachments": [],
                        "audio": None,
                    }
                    stored = await append_user_turn(
                        session,
                        question=turn["prompt"],
                        attachments=turn["attachments"] or None,
                        message_ids=turn["message_ids"],
                    )
                    if stored:
                        await runtime.process_turn_locked(
                            client,
                            session,
                            config=config,
                            turn=turn,
                        )
            except Exception:
                notify_queue.requeue_notification(path)
                raise
            else:
                notify_queue.ack_notification(path)
        try:
            await asyncio.wait_for(notifications_stop.wait(), timeout=1.0)
        except TimeoutError:
            continue


@client.event(ConnectedEv)
async def _on_connected(_: NewAClient, __: ConnectedEv) -> None:
    logger.info("Faltoobot connected to WhatsApp")


@client.event(PairStatusEv)
async def _on_pair_status(_: NewAClient, event: PairStatusEv) -> None:
    logger.info("Pair status: %s", event.Status)


async def _handle_debounce_timer(
    current_client: NewAClient,
    *,
    chat_key: str,
    turn: runtime.Turn,
) -> None:
    async with chat_locks[chat_key]:
        session = get_session(chat_key=chat_key)
        await runtime.process_turn_locked(
            current_client,
            session,
            config=config,
            turn=turn,
        )


async def _handle_message(current_client: NewAClient, event: MessageEv) -> None:
    source = event.Info.MessageSource
    chat_jid = Jid2String(source.Chat)
    chat_key = normalize_chat(chat_jid)

    # comment: same-chat turn normalization and history updates must stay serialized
    # so album buffers and session writes never race within one chat.
    async with chat_locks[chat_key]:
        session = get_session(chat_key=chat_key)
        # comment: `turn` is the normalized user input for one model run, like the
        # combined question text plus any attachments gathered from the event(s).
        turn = await runtime.get_turn_locked(
            current_client,
            event,
            config=config,
            session=session,
            pending_albums=pending_albums,
        )
        if turn is None:
            return
        stored = True
        if turn["prompt"] not in runtime.SLASH_COMMANDS:
            stored = await append_user_turn(
                session,
                question=turn["prompt"],
                attachments=turn["attachments"] or None,
                message_ids=turn["message_ids"],
            )
    if not stored:
        return
    current_timer = debounce_timers.pop(chat_key, None)
    if current_timer is not None:
        current_timer.cancel()
    loop = asyncio.get_running_loop()

    def start_debounce_timer() -> None:
        task = asyncio.create_task(
            _handle_debounce_timer(
                current_client,
                chat_key=chat_key,
                turn=turn,
            )
        )
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    debounce_timers[chat_key] = loop.call_later(DEBOUNCE_SECONDS, start_debounce_timer)


@client.event(MessageEv)
async def _on_message(current_client: NewAClient, event: MessageEv) -> None:
    # comment: keep the Neonize event handler small and return quickly. `_handle_message`
    # does parsing, per-chat locking, typing, model call, and reply.
    task = asyncio.create_task(_handle_message(current_client, event))
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def main(this_config: Config | None = None) -> None:
    global \
        client, \
        config, \
        tasks, \
        chat_locks, \
        debounce_timers, \
        pending_albums, \
        notifications_stop

    config = this_config or build_config()
    login.configure_logging(config.log_file)
    tasks = set()
    chat_locks = defaultdict(asyncio.Lock)
    debounce_timers = {}
    pending_albums = {}
    notifications_stop = asyncio.Event()
    notify_task = asyncio.create_task(_start_polling_notifications())

    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, lambda: asyncio.create_task(on_exit()))

    logger.info("Using session DB: %s", config.session_db)
    logger.info("Using sessions dir: %s", config.sessions_dir)
    await client.connect()
    await client.idle()

    # comment: stop the background notify-queue poller and wait for its last loop to finish.
    notifications_stop.set()
    await notify_task

    if tasks:
        # comment: wait for already-started message handlers to settle during shutdown.
        await asyncio.gather(*tasks, return_exceptions=True)
