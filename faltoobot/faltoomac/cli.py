import argparse

from faltoobot.config import Config


def run_macos_chat(config: Config | None = None, name: str | None = None) -> None:
    from .app import run_macos_chat_app

    run_macos_chat_app(config=config, name=name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="faltoomac")
    parser.add_argument("--name", help="optional session name")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_macos_chat(name=args.name)
    return 0
