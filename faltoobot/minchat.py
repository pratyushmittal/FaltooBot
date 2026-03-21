import json
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from textual import getters
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical, VerticalScroll
from textual.widgets import Markdown, TextArea

from faltoobot import sessions
from faltoobot.chat.terminal import open_in_default_editor
from faltoobot.gpt_utils import MessageItem
from faltoobot.placeholders import get_random_placeholder
from faltoobot.stream import get_event_text


def get_text(value: Any) -> str:
    match value:
        case str(text):
            return text.strip()
        case list(parts):
            return "\n".join(
                text
                for part in parts
                if isinstance(part, dict)
                for text in [str(part.get("text") or "").strip()]
                if text
            )
        case _:
            return ""


def clip_lines(text: str, max_lines: int = 5) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join([*lines[: max_lines - 1], "..."])


def get_tool_call_text(name: str, arguments: str) -> str:
    if arguments.strip():
        try:
            arguments = json.dumps(json.loads(arguments), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            pass
        return clip_lines(f"{name}\n{arguments}")
    return name


def get_tool_text(item: MessageItem) -> str | None:
    match item:
        case {"type": "function_call", "name": str(name), "arguments": str(arguments)}:
            return get_tool_call_text(name, arguments)
        case {
            "type": "web_search_call",
            "action": {"query": str(query)},
        }:
            return f"web search\n{query.strip()}" if query.strip() else "web search"
        case {"type": str(item_type)} if item_type.endswith("_call"):
            return item_type.replace("_", " ")
        case _:
            return None


def get_item_text(item: MessageItem) -> str:
    if text := get_tool_text(item):
        return text
    match item:
        case {"type": "message", "content": content}:
            return get_text(content)
        case {"type": "reasoning", "summary": summary}:
            return get_text(summary)
        case {"type": "function_call_output"}:
            return ""
        case _:
            return ""


class FaltooChatApp(App[None]):
    CSS = """
    App {
        background: $background;
        color: $text;
    }

    Screen {
        layout: vertical;
        background: $background;
    }

    #shell {
        width: 1fr;
        max-width: 80;
        height: 1fr;
    }

    #transcript {
        height: 1fr;
        overflow-y: auto;
        padding: 1 2 0 2;
    }

    #composer {
        height: 6;
        margin: 1 2 2 2;
        padding: 0 1;
        background: $surface;
        border: tall $primary;
        color: $text;
    }

    Markdown {
        margin: 0 0 1 0;
        padding: 0 1;
        background: $surface;
        border-left: wide $panel;
        color: $text;
    }

    .user {
        background: $primary 15%;
        border-left: wide $primary;
        color: $text;
    }

    .thinking {
        background: $accent 12%;
        border-left: wide $accent;
        color: $text;
    }

    .tool {
        background: $warning 12%;
        border-left: wide $warning;
        color: $text;
    }

    .answer {
        background: $success 12%;
        border-left: wide $success;
        color: $text;
    }

    .unknown {
        background: $error 12%;
        border-left: wide $error;
        color: $text;
    }
    """

    def __init__(self, session: sessions.Session) -> None:
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="shell"):
                yield VerticalScroll(id="transcript")
                yield Composer(
                    id="composer",
                    text="",
                    soft_wrap=True,
                    show_line_numbers=False,
                    highlight_cursor_line=False,
                    placeholder=get_random_placeholder(),
                )

    async def on_mount(self) -> None:
        await self.load_messages()

    def scroll_transcript_end(self, transcript: VerticalScroll) -> None:
        self.call_after_refresh(
            transcript.scroll_end,
            animate=False,
            immediate=True,
        )

    async def load_messages(self) -> None:
        messages_json = sessions.get_messages(self.session)
        transcript = self.query_one("#transcript", VerticalScroll)
        await transcript.remove_children()
        blocks = []
        for message in messages_json["messages"]:
            if not isinstance(message, dict):
                continue
            text = get_item_text(message)
            if text:
                match message:
                    case {"type": "message", "role": "user"}:
                        classes = "user"
                    case {"type": "message"}:
                        classes = "answer"
                    case {"type": "reasoning"}:
                        classes = "thinking"
                    case {"type": "function_call"} | {"type": "function_call_output"}:
                        classes = "tool"
                    case _:
                        classes = ""
                blocks.append(Markdown(text, classes=classes))
        if not blocks:
            blocks = [
                Markdown(
                    "_No messages yet. The void is waiting._",
                    classes="thinking",
                )
            ]
        await transcript.mount(*blocks)
        self.scroll_transcript_end(transcript)

        composer = self.query_one("#composer", Composer)
        composer.focus()

    async def handle_command(self, question: str) -> bool:
        match question:
            case "/tree":
                open_in_default_editor(sessions.get_messages_path(self.session))
                return True
            case "/reset":
                workspace = Path(sessions.get_messages(self.session)["workspace"])
                self.session = sessions.get_session(
                    chat_key=self.session[0],
                    session_id=str(uuid4()),
                    workspace=workspace,
                )
                await self.load_messages()
                return True
            case _:
                return False

    async def stream_reply(
        self,
        transcript: VerticalScroll,
        question: str,
    ) -> None:
        block: Markdown | None = None
        async for event in sessions.get_answer_streaming(
            session=self.session,
            question=question,
        ):
            is_new, classes, text = get_event_text(event)
            if not text:
                if is_new:
                    block = None
                continue
            if block is None or is_new:
                block = Markdown(text, classes=classes)
                await transcript.mount(block)
            else:
                await block.append(text)

            is_at_bottom = transcript.max_scroll_y - transcript.scroll_y <= 3  # noqa: PLR2004
            if is_at_bottom:
                self.scroll_transcript_end(transcript)

    async def submit_message(self) -> None:
        composer = self.query_one("#composer", Composer)
        transcript = self.query_one("#transcript", VerticalScroll)
        question = composer.text.strip()
        if not question:
            return

        composer.load_text("")
        if await self.handle_command(question):
            return
        await transcript.mount(Markdown(question, classes="user"))
        is_at_bottom = transcript.max_scroll_y - transcript.scroll_y <= 3  # noqa: PLR2004
        if is_at_bottom:
            self.scroll_transcript_end(transcript)
        await self.stream_reply(transcript, question)


class Composer(TextArea):
    BINDINGS = [
        Binding("enter", "composer_enter", "Submit", priority=True),
    ]
    BINDING_GROUP_TITLE = "Chat"

    # for type-checking
    app = getters.app(FaltooChatApp)

    def action_composer_enter(self) -> None:
        self.app.run_worker(self.app.submit_message(), exclusive=True)


def main() -> int:
    session_id = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        FaltooChatApp(
            session=sessions.get_session(
                chat_key=sessions.get_dir_chat_key(Path.cwd()),
                session_id=session_id,
                workspace=Path.cwd(),
            ),
        ).run()
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
