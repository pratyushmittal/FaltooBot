import argparse
import re
from datetime import datetime
from pathlib import Path

from openai import AsyncOpenAI
from textual import events
from textual.app import App, ComposeResult
from textual.document._document import Selection
from textual.widgets import Input, Static, TextArea

from faltoobot.agent import reply
from faltoobot.config import Config, build_config
from faltoobot.store import Session, add_turn, create_cli_session, reset_session, session_items

TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]+")


def default_session_name() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def help_text() -> str:
    return "Commands: /help, /reset, /exit"


def session_name(name: str | None) -> str:
    return f"CLI {name or default_session_name()}"


def thinking_mode(config: Config) -> str:
    return config.openai_thinking


def status_text(config: Config) -> str:
    return f"model: {config.openai_model}  thinking: {thinking_mode(config)}"


class TranscriptArea(TextArea):
    async def on_click(self, event: events.Click) -> None:
        if event.chain != 2:
            return
        row, column = self.get_target_document_location(event)
        line = self.document[row]
        if not line:
            return
        index = min(column, len(line) - 1)
        token = next(
            (match for match in TOKEN_PATTERN.finditer(line) if match.start() <= index < match.end()),
            None,
        )
        if token is None:
            return
        self.selection = Selection((row, token.start()), (row, token.end()))
        self.focus()


class FaltoochatApp(App[None]):
    CSS = """
    Screen {
        layout: vertical;
    }

    #messages {
        height: 1fr;
        border: round $accent;
    }

    Input {
        margin-top: 1;
    }

    #status {
        color: $text-muted;
        margin: 0 1;
        height: auto;
    }
    """

    def __init__(self, config: Config, name: str | None = None) -> None:
        super().__init__()
        self.config = config
        self.chat_name = session_name(name)
        self.session: Session | None = None
        self.client: AsyncOpenAI | None = None
        self.lines: list[str] = []

    def compose(self) -> ComposeResult:
        yield TranscriptArea("", id="messages", read_only=True, soft_wrap=True, show_cursor=False)
        yield Input(placeholder="Type a message or /help", id="prompt")
        yield Static(status_text(self.config), id="status")

    async def on_mount(self) -> None:
        if not self.config.openai_api_key:
            raise RuntimeError(f"openai.api_key is missing. Add it to {self.config.config_file}")
        self.session = create_cli_session(
            self.config.sessions_dir,
            self.chat_name,
            workspace=Path.cwd(),
        )
        self.client = AsyncOpenAI(api_key=self.config.openai_api_key)
        self.write_line(f"session: {self.session.name} ({self.session.id})")
        self.write_line(f"workspace: {self.session.workspace}")
        self.write_line(help_text())

    async def on_unmount(self) -> None:
        if self.client:
            await self.client.close()

    def message_log(self) -> TranscriptArea:
        return self.query_one("#messages", TranscriptArea)

    def prompt(self) -> Input:
        return self.query_one("#prompt", Input)

    def write_line(self, text: str) -> None:
        self.lines.append(text)
        log = self.message_log()
        log.load_text("\n".join(self.lines))
        log.move_cursor((len(self.lines), 0))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        event.input.value = ""
        if not prompt:
            return
        if prompt == "/help":
            self.write_line(help_text())
            return
        if prompt == "/reset":
            if self.session:
                self.session = reset_session(self.session)
            self.write_line("memory cleared")
            return
        if prompt == "/exit":
            self.exit()
            return
        await self.handle_prompt(prompt)

    async def handle_prompt(self, prompt: str) -> None:
        if not self.session or not self.client:
            raise RuntimeError("chat session is not ready")
        input_box = self.prompt()
        input_box.disabled = True
        self.write_line(f"you> {prompt}")
        self.session = add_turn(self.session, "user", prompt)
        try:
            result = await reply(
                self.client,
                self.config,
                self.session,
                session_items(self.session),
            )
        except Exception as exc:
            self.write_line(f"error> {exc}")
        else:
            answer = result["text"]
            self.session = add_turn(
                self.session,
                "assistant",
                answer,
                items=result["output_items"],
                usage=result["usage"],
            )
            self.write_line(f"bot> {answer}")
        finally:
            input_box.disabled = False
            input_box.focus()


def build_chat_app(config: Config | None = None, name: str | None = None) -> FaltoochatApp:
    return FaltoochatApp(config or build_config(), name=name)


async def run_chat(config: Config | None = None, name: str | None = None) -> None:
    await build_chat_app(config, name=name).run_async()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="faltoochat")
    parser.add_argument("--name", help="optional session name")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build_chat_app(name=args.name).run()
