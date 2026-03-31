import asyncio
import logging
import signal
from collections import defaultdict
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from neonize.aioze.client import NewAClient
from neonize.aioze.events import ConnectedEv, MessageEv, PairStatusEv

from faltoobot.config import Config, build_config

from . import runtime

logger = logging.getLogger("faltoobot")
AUTH_STOP_DELAY = 0.5

__all__ = [
    "configure_logging",
    "run_auth",
    "run_bot",
]


def _quiet_whatsapp_logs() -> None:
    for name in (
        "whatsmeow",
        "whatsmeow.Client",
        "whatsmeow.Client.Socket",
        "Whatsmeow.Database",
    ):
        logging.getLogger(name).setLevel(logging.ERROR)


def configure_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler()],
        force=True,
    )
    _quiet_whatsapp_logs()


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
    client = NewAClient(str(config.session_db))
    chat_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
    pending_albums: dict[str, runtime.PendingAlbum] = {}
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
            runtime.process_message(
                current_client,
                event,
                config=config,
                chat_locks=chat_locks,
                pending_albums=pending_albums,
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
