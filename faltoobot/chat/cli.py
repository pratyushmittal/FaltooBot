import argparse

from faltoobot.config import Config

from .app import FaltooChatApp
from .terminal import terminal_background_dark


async def run_chat(
    config: Config | None = None,
    name: str | None = None,
    *,
    new_session: bool = False,
    prompt: str | None = None,
) -> None:
    await FaltooChatApp(
        config=config,
        name=name,
        new_session=new_session,
        initial_prompt=prompt,
        terminal_dark=terminal_background_dark(),
    ).run_async()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="faltoochat")
    parser.add_argument("prompt", nargs="?", help="optional prompt to submit on launch")
    parser.add_argument("--name", help="optional session name")
    parser.add_argument(
        "--new-session", action="store_true", help="start a fresh session"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        FaltooChatApp(
            name=args.name,
            new_session=args.new_session,
            initial_prompt=args.prompt,
            terminal_dark=terminal_background_dark(),
        ).run()
    except KeyboardInterrupt:
        return 130
    return 0
