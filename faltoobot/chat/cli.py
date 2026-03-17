import argparse

from faltoobot.config import Config

from .app import build_chat_app
from .terminal import terminal_background_dark


async def run_chat(config: Config | None = None, name: str | None = None) -> None:
    await build_chat_app(
        config=config, name=name, terminal_dark=terminal_background_dark()
    ).run_async()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="faltoochat")
    parser.add_argument("--name", help="optional session name")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        build_chat_app(name=args.name, terminal_dark=terminal_background_dark()).run()
    except KeyboardInterrupt:
        return 130
    return 0
