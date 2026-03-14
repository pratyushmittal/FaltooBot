import argparse
import asyncio
from datetime import datetime

from openai import AsyncOpenAI

from faltoobot.agent import reply
from faltoobot.config import Config, build_config
from faltoobot.store import add_turn, create_cli_session, recent_items, reset_session


def default_session_name() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def help_text() -> str:
    return "Commands: /help, /reset, /exit"


async def read_prompt() -> str:
    return await asyncio.to_thread(input, "you> ")


async def run_chat(config: Config | None = None, name: str | None = None) -> None:
    config = config or build_config()
    if not config.openai_api_key:
        raise RuntimeError(f"openai.api_key is missing. Add it to {config.config_file}")

    session = create_cli_session(
        config.sessions_dir,
        name=f"CLI {name or default_session_name()}",
    )
    openai_client = AsyncOpenAI(api_key=config.openai_api_key)
    print(f"session: {session.name} ({session.id})")
    print(f"workspace: {session.workspace}")
    print(help_text())

    try:
        while True:
            try:
                prompt = (await read_prompt()).strip()
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print()
                break

            if not prompt:
                continue
            if prompt == "/help":
                print(help_text())
                continue
            if prompt == "/reset":
                session = reset_session(session)
                print("memory cleared")
                continue
            if prompt == "/exit":
                break

            session = add_turn(session, "user", prompt)
            try:
                result = await reply(
                    openai_client,
                    config,
                    session,
                    recent_items(session, config.max_history_messages),
                )
            except Exception as exc:
                print(f"error> {exc}")
                continue
            answer = result["text"]
            session = add_turn(session, "assistant", answer, items=result["output_items"])
            print(f"bot> {answer}")
    finally:
        await openai_client.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="faltoochat")
    parser.add_argument("--name", help="optional session name")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(run_chat(name=args.name))
