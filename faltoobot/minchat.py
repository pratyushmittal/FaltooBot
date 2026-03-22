import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from textual import events, getters
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical, VerticalScroll
from textual.widgets import Markdown, Static, TextArea

from faltoobot import sessions
from faltoobot.chat.terminal import open_in_default_editor
from faltoobot.gpt_utils import MessageItem
from faltoobot.messages_rendering import get_item_text
from faltoobot.paste import pasted_image_path, save_clipboard_image
from faltoobot.placeholders import get_random_placeholder
from faltoobot.stream import get_event_text

TRANSCRIPT_BOTTOM_THRESHOLD = 6


class FaltooChatApp(App[None]):
    CSS = """
    App {
        color: $text;
    }

    Screen {
        layout: vertical;
        layers: base content;
    }

    #backdrop {
        layer: base;
        width: 1fr;
        height: 1fr;
    }

    #shell {
        layer: content;
        width: 1fr;
        height: 1fr;
    }

    #transcript {
        width: 1fr;
        height: 1fr;
        align-horizontal: center;
        overflow-y: auto;
        padding: 1 2 0 2;
    }

    #composer {
        width: 1fr;
        max-width: 80;
        height: 6;
        margin: 1 2 2 2;
        padding: 0 1;
        background: $surface;
        border: tall $primary;
        color: $text;
    }

    Markdown {
        width: 1fr;
        max-width: 80;
        margin: 0 0 1 0;
        padding: 0 1;
        border-left: wide $panel;
        color: $text;
    }

    /* Textual adds bottom margin to every paragraph. Remove it for the last
       paragraph so message blocks keep their external gap without looking taller
       than the content inside them. */
    Markdown > MarkdownParagraph:last-child {
        margin: 0;
    }

    .user {
        background: $primary 15%;
        border-left: wide $primary;
        color: $text;
    }

    .thinking {
        border-left: none;
        color: $text-muted;
    }

    .tool {
        background: $warning 8%;
        border-left: none;
        color: $text-muted;
    }

    .answer {
        border-left: none;
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
        yield Static(id="backdrop")
        with Vertical(id="shell"):
            yield VerticalScroll(id="transcript")
            with Center():
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

    async def load_messages(self) -> None:
        messages_json = sessions.get_messages(self.session)
        transcript = self.query_one("#transcript", VerticalScroll)
        await transcript.remove_children()
        blocks = []
        for message in messages_json["messages"]:
            if rendering := get_item_text(message):
                text, classes = rendering
                blocks.append(Markdown(text, classes=classes))
        if not blocks:
            blocks = [
                Markdown(
                    "_No messages yet. The void is waiting._",
                    classes="thinking",
                )
            ]
        await transcript.mount(*blocks)
        self.call_after_refresh(
            transcript.scroll_end,
            animate=False,
            immediate=True,
        )

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
        attachments: list[sessions.Attachment] | None = None,
    ) -> None:
        block: Markdown | None = None
        async for event in sessions.get_answer_streaming(
            session=self.session,
            question=question,
            attachments=attachments,
        ):
            is_new, classes, text = get_event_text(event)
            if not text:
                if is_new:
                    block = None
                continue

            should_continue = (
                transcript.max_scroll_y - transcript.scroll_y
                <= TRANSCRIPT_BOTTOM_THRESHOLD
            )

            if block is None or is_new:
                block = Markdown(text, classes=classes)
                await transcript.mount(block)
            else:
                await block.append(text)

            if should_continue:
                self.call_after_refresh(
                    transcript.scroll_end,
                    animate=False,
                    immediate=True,
                )

    async def submit_message(self) -> None:
        composer = self.query_one("#composer", Composer)
        transcript = self.query_one("#transcript", VerticalScroll)
        question = composer.text.strip()
        attachments = composer.take_attachments()
        if not question and not attachments:
            return

        composer.load_text("")
        if await self.handle_command(question):
            return

        content: list[dict[str, str]] = [
            *([{"type": "input_text", "text": question}] if question else []),
            *({"type": "input_image"} for _ in attachments),
        ]
        message_item: MessageItem = {
            "type": "message",
            "role": "user",
            "content": content,
        }
        preview_text, preview_classes = get_item_text(message_item)  # type: ignore
        await transcript.mount(Markdown(preview_text, classes=preview_classes))

        self.call_after_refresh(
            transcript.scroll_end,
            animate=False,
            immediate=True,
        )

        await self.stream_reply(
            transcript,
            question,
            attachments=attachments,
        )


class Composer(TextArea):
    BINDINGS = [
        Binding("enter", "composer_enter", "Submit", priority=True),
        Binding("shift+enter", "newline", "New line", priority=True),
    ]
    BINDING_GROUP_TITLE = "Chat"

    # for type-checking
    app = getters.app(FaltooChatApp)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.attachments: list[sessions.Attachment] = []

    def attach_image(self, path: sessions.Attachment) -> None:
        self.attachments.append(path)
        count = len(self.attachments)
        self.border_title = (
            f"{count} attachment" if count == 1 else f"{count} attachments"
        )
        self.focus()

    def take_attachments(self) -> list[sessions.Attachment]:
        attachments = list(self.attachments)
        self.attachments.clear()
        self.border_title = ""
        return attachments

    async def on_paste(self, event: events.Paste) -> None:
        if self.read_only:
            return
        if path := pasted_image_path(self.app.session, event.text):
            event.stop()
            event.prevent_default()
            self.attach_image(path)

    def action_paste(self) -> None:
        if self.read_only:
            return
        if path := save_clipboard_image(self.app.session):
            self.attach_image(path)
            return
        super().action_paste()

    def action_composer_enter(self) -> None:
        self.app.run_worker(self.app.submit_message(), exclusive=True)

    def action_newline(self) -> None:
        self.insert("\n")


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
