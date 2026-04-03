import asyncio
import logging
from pathlib import Path

from neonize.aioze.client import NewAClient
from neonize.aioze.events import ConnectedEv, PairStatusEv

from faltoobot.config import Config, build_config

logger = logging.getLogger("faltoobot")
AUTH_STOP_DELAY = 0.5


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


async def run_auth(config: Config | None = None) -> None:
    config = config or build_config()
    configure_logging(config.log_file)
    client = NewAClient(str(config.session_db))
    logger.info("Starting auth flow. Scan the QR code shown below.")
    await wait_for_login(client)
