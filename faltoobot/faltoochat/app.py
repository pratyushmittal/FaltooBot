import argparse
from pathlib import Path
from typing import Any
from uuid import uuid4

from textual import events, getters
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical, VerticalScroll
from textual.widgets import Markdown, Static, TextArea

from faltoobot import sessions
from faltoobot.faltoochat.terminal import (
    open_in_default_editor,
    textual_theme_from_terminal,
)
from faltoobot.gpt_utils import MessageItem
from .messages_rendering import get_item_text, visible_thinking_text
from .paste import pasted_image_path, save_clipboard_image
from .placeholders import get_random_placeholder
from .stream import get_event_text
from .widgets import QueueWidget

STARTUP_MESSAGES_LIMIT = 100


def get_local_user_message_item(
    question: str,
    attachments: list[sessions.Attachment],
) -> MessageItem:
    # comment: this local MessageItem is not appended to messages_json. It mirrors the
    # user item that later goes through session handling, where local file attachments
    # are uploaded before the API call.
    content: list[dict[str, Any]] = [
        *([{"type": "input_text", "text": question}] if question else []),
        *({"type": "input_image", "image_path": str(path)} for path in attachments),
    ]
    return {
        "type": "message",
        "role": "user",
        "content": content,
    }


def decompose_local_message_item(
    message_item: MessageItem,
) -> tuple[str, list[sessions.Attachment]]:
    question = ""
    attachments: list[sessions.Attachment] = []
    for part in message_item["content"]:
        if part.get("type") == "input_text":
            question += str(part.get("text") or "")
        if part.get("type") == "input_image" and isinstance(
            part.get("image_path"), str
        ):
            attachments.append(part["image_path"])
    return question, attachments


async def _write_stream_chunk(
    block: Markdown,
    classes: str,
    text: str,
    block_raw_text: str,
    answer_stream: Any | None,
) -> str:
    if classes == "thinking":
        block_raw_text += text
        await block.update(visible_thinking_text(block_raw_text))
        return block_raw_text
    if classes == "answer" and answer_stream is not None:
        await answer_stream.write(text)
        return block_raw_text
    await block.append(text)
    return block_raw_text


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
        border: round transparent;
    }

    #transcript:focus {
        border: round $primary;
    }

    #footer {
        width: 1fr;
        max-width: 84;
        height: auto;
    }

    #composer {
        width: 1fr;
        height: 7;
        margin: 1 0 1 0;
        padding: 0 0 0 1;
        background: $background;
        border: round $panel;
        color: $text;
    }

    #composer:focus {
        border: round $primary;
    }

    Markdown {
        width: 1fr;
        max-width: 80;
        margin: 0 0 1 0;
        padding: 0 1;
        border-left: wide $panel;
        color: $text;
    }

    .history-summary {
        width: 1fr;
        max-width: 80;
        margin: 0 0 1 0;
        padding: 0 1;
        color: $text-muted;
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
        max-height: 6;
        overflow-y: hidden;
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

    def __init__(
        self,
        session: sessions.Session,
        *,
        initial_prompt: str | None = None,
    ) -> None:
        super().__init__()
        if theme := textual_theme_from_terminal():
            self.theme = theme
        self.session = session
        self.initial_prompt = (initial_prompt or "").strip()
        self.is_answering = False

    def queue(self) -> QueueWidget:
        return self.query_one(QueueWidget)

    def compose(self) -> ComposeResult:
        yield Static(id="backdrop")
        with Vertical(id="shell"):
            yield VerticalScroll(id="transcript")
            with Center():
                with Vertical(id="footer"):
                    yield QueueWidget()
                    yield Composer(
                        id="composer",
                        text="",
                        soft_wrap=True,
                        show_line_numbers=False,
                        highlight_cursor_line=False,
                        placeholder=get_random_placeholder(),
                    )

    async def on_mount(self) -> None:
        await self.load_messages(recent_limit=STARTUP_MESSAGES_LIMIT)
        await self.queue().refresh_queue()
        if self.initial_prompt:
            self.run_worker(
                self.submit_message(
                    get_local_user_message_item(self.initial_prompt, [])
                ),
                exclusive=True,
            )
            self.initial_prompt = ""

    async def load_messages(
        self,
        *,
        recent_limit: int | None = None,
    ) -> None:
        messages_json = sessions.get_messages(self.session)
        transcript = self.query_one("#transcript", VerticalScroll)
        blocks = []
        messages = messages_json["messages"]
        if recent_limit is not None and len(messages) > recent_limit:
            blocks.append(
                Static(
                    f"Showing last {recent_limit} of {len(messages)} messages. [@click=app.load_all_messages()]load all[/]",
                    classes="history-summary",
                )
            )
            messages = messages[-recent_limit:]
        for message in messages:
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
        await transcript.remove_children()
        await transcript.mount(*blocks)
        self.call_after_refresh(
            transcript.scroll_end,
            animate=False,
            immediate=True,
        )

        composer = self.query_one("#composer", Composer)
        composer.focus()

    async def action_load_all_messages(self) -> None:
        await self.load_messages()

    async def stream_reply(
        self,
        transcript: VerticalScroll,
        question: str,
        attachments: list[sessions.Attachment] | None = None,
    ) -> None:
        block: Markdown | None = None
        answer_stream: Any | None = None
        block_raw_text = ""
        transcript.anchor()
        async for event in sessions.get_answer_streaming(
            session=self.session,
            question=question,
            attachments=attachments,
        ):
            is_new, classes, text = get_event_text(event)
            if not text:
                if is_new:
                    if answer_stream is not None:
                        await answer_stream.stop()
                        answer_stream = None
                    block = None
                    block_raw_text = ""
                continue
            if block is None or is_new:
                if answer_stream is not None:
                    await answer_stream.stop()
                    answer_stream = None
                block = Markdown("", classes=classes)
                block_raw_text = ""
                await transcript.mount(block)
                if classes == "answer":
                    answer_stream = Markdown.get_stream(block)
            block_raw_text = await _write_stream_chunk(
                block,
                classes,
                text,
                block_raw_text,
                answer_stream,
            )
        if answer_stream is not None:
            await answer_stream.stop()

    async def submit_message(self, message_item: MessageItem):
        # render user message
        transcript = self.query_one("#transcript", VerticalScroll)
        preview_text, preview_classes = get_item_text(message_item)  # type: ignore
        await transcript.mount(Markdown(preview_text, classes=preview_classes))
        self.call_after_refresh(
            transcript.scroll_end,
            animate=False,
            immediate=True,
        )

        # stream reply
        self.is_answering = True
        composer = self.query_one("#composer", Composer)
        composer.border_subtitle = "answering"
        try:
            await self.stream_reply(
                transcript,
                *decompose_local_message_item(message_item),
            )
        finally:
            self.is_answering = False
            composer.border_subtitle = ""
            composer.focus()
        await self.queue().submit_next_message()


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

    async def handle_command(self, question: str) -> bool:
        match question:
            case "/tree":
                open_in_default_editor(sessions.get_messages_path(self.app.session))
                return True
            case "/reset":
                workspace = Path(sessions.get_messages(self.app.session)["workspace"])
                self.app.session = sessions.get_session(
                    chat_key=self.app.session[0],
                    session_id=str(uuid4()),
                    workspace=workspace,
                )
                await self.app.load_messages()
                await self.app.queue().refresh_queue()
                return True
            case _:
                return False

    async def action_composer_enter(self) -> None:
        question = self.text.strip()
        attachments = self.take_attachments()
        if not question and not attachments:
            return

        self.load_text("")
        if await self.handle_command(question):
            return

        # add to queue if answering
        message_item = get_local_user_message_item(question, attachments)
        if self.app.is_answering:
            await self.app.queue().add_to_queue(message_item)
            self.focus()
            return

        # exclusive=True tells Textual to cancel all previous workers before starting the new one
        self.app.run_worker(self.app.submit_message(message_item), exclusive=True)

    def action_newline(self) -> None:
        self.insert("\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="faltoochat")
    parser.add_argument("prompt", nargs="?", help="optional prompt to submit on launch")
    parser.add_argument(
        "--new-session",
        action="store_true",
        help="start a fresh session",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session_id = str(uuid4()) if args.new_session else None
    try:
        FaltooChatApp(
            session=sessions.get_session(
                chat_key=sessions.get_dir_chat_key(Path.cwd()),
                session_id=session_id,
                workspace=Path.cwd(),
            ),
            initial_prompt=args.prompt,
        ).run()
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
