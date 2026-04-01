import argparse
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any
from uuid import uuid4

from textual import events, getters
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical, VerticalScroll
from textual.widgets import (
    Footer,
    Markdown,
    OptionList,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)
from textual.widgets.option_list import Option

from faltoobot import sessions
from faltoobot.config import load_textual_theme, save_textual_theme
from faltoobot.faltoochat.terminal import (
    open_in_default_editor,
    textual_theme_from_terminal,
)
from faltoobot.gpt_utils import MessageItem
from faltoobot.session_utils import (
    decompose_local_message_item,
    get_local_user_message_item,
)

from .messages_rendering import (
    SHELL_COMMAND_SEPARATOR,
    get_item_text,
    visible_thinking_text,
)
from .paste import pasted_image_path, save_clipboard_image
from .placeholders import get_random_placeholder
from .review import ReviewView
from .stream import get_event_text
from .widgets import QueueWidget

STARTUP_MESSAGES_LIMIT = 100
AUTO_SCROLL_RESUME_LINES = 3
SLASH_COMMANDS = {
    "/reset": "start a fresh session",
    "/tree": "open the current session messages file",
}


def _render_blocks(text: str, classes: str) -> list[Markdown]:
    if classes != "tool" or SHELL_COMMAND_SEPARATOR not in text:
        return [Markdown(text, classes=classes)]
    summary, command = text.split(SHELL_COMMAND_SEPARATOR, maxsplit=1)
    blocks = [Markdown(summary, classes="tool tool-summary")]
    if command.strip():
        blocks.append(Markdown(command.strip(), classes="tool tool-command"))
    return blocks


async def _stop_answer_stream(answer_stream: Any | None) -> None:
    if answer_stream is not None:
        await answer_stream.stop()


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
    BINDINGS = [
        Binding("ctrl+1", "show_chat_tab", "Chat", priority=True, show=False),
        Binding("ctrl+2", "show_review_tab", "Review", priority=True, show=False),
        Binding(
            "ctrl+r",
            "toggle_review_tab",
            "Toggle Review",
            priority=True,
            show=False,
        ),
    ]

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

    #tabs {
        width: 1fr;
        height: 1fr;
    }

    TabPane {
        height: 1fr;
        padding: 0;
    }

    #chat-shell {
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

    #slash-commands {
        width: 1fr;
        max-width: 84;
        height: auto;
        max-height: 6;
        margin: 0 0 1 0;
        background: $surface;
        border: round $panel;
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
        background: $warning 8%;
        border-left: none;
        color: $text-muted;
    }

    .tool-summary {
        background: $warning 32%;
        color: $text;
        margin: 0 0 0 0;
        padding: 0 1;
    }

    .tool-command {
        max-height: 4;
        overflow-y: hidden;
        margin: 0 0 1 0;
        padding: 1 2 0 2;
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
        self._persist_theme_changes = False
        super().__init__()
        if (saved_theme := load_textual_theme()) in self.available_themes:
            self.theme = saved_theme
        elif theme := textual_theme_from_terminal():
            self.theme = theme
        self._persist_theme_changes = True
        self.session = session
        self.workspace = Path(sessions.get_messages(session)["workspace"])
        self.initial_prompt = (initial_prompt or "").strip()
        self.is_answering = False

    def queue(self) -> QueueWidget:
        return self.query_one(QueueWidget)

    def _watch_theme(self, theme_name: str) -> None:
        super()._watch_theme(theme_name)
        if not self._persist_theme_changes:
            return
        save_textual_theme(theme_name)

    def tabs(self) -> TabbedContent:
        return self.query_one("#tabs", TabbedContent)

    def focus_composer(self) -> None:
        self.set_focus(self.query_one("#composer", Composer), scroll_visible=False)

    def action_show_chat_tab(self) -> None:
        self.tabs().active = "chat-tab"
        transcript = self.query_one("#transcript", VerticalScroll)
        self.call_after_refresh(transcript.scroll_end, animate=False)
        self.call_after_refresh(self.focus_composer)

    def action_show_review_tab(self) -> None:
        self.tabs().active = "review-tab"

    def action_toggle_review_tab(self) -> None:
        if self.tabs().active == "review-tab":
            self.action_show_chat_tab()
            return
        self.action_show_review_tab()

    async def on_tabbed_content_tab_activated(
        self,
        event: TabbedContent.TabActivated,
    ) -> None:
        if event.tabbed_content.id != "tabs":
            return
        if event.pane.id != "review-tab":
            return
        review = self.query_one(ReviewView)
        self.call_after_refresh(
            lambda: self.run_worker(
                review.refresh_files(),
                group="review-refresh",
                exclusive=True,
            )
        )

    def compose(self) -> ComposeResult:
        yield Static(id="backdrop")
        with Vertical(id="shell"):
            with TabbedContent(initial="chat-tab", id="tabs"):
                with TabPane("Chat", id="chat-tab"):
                    with Vertical(id="chat-shell"):
                        yield VerticalScroll(id="transcript")
                        with Center():
                            with Vertical(id="footer"):
                                yield QueueWidget()
                                yield OptionList(id="slash-commands", markup=False)
                                yield Composer(
                                    id="composer",
                                    text="",
                                    soft_wrap=True,
                                    show_line_numbers=False,
                                    highlight_cursor_line=False,
                                    placeholder=get_random_placeholder(),
                                )
                yield ReviewView()
            yield Footer()

    def on_option_list_option_selected(
        self,
        event: OptionList.OptionSelected,
    ) -> None:
        if event.option_list.id != "slash-commands":
            return
        self.query_one("#composer", Composer).apply_selected_slash_command(
            event.option_index
        )

    async def on_mount(self) -> None:
        self.query_one("#slash-commands", OptionList).display = False
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
                blocks.extend(_render_blocks(text, classes))
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
        raw_text = ""
        transcript.anchor()

        async for event in sessions.get_answer_streaming(
            session=self.session,
            question=question,
            attachments=attachments,
        ):
            is_new, classes, text = get_event_text(event)
            if not text:
                if is_new:
                    await _stop_answer_stream(answer_stream)
                    answer_stream, block, raw_text = None, None, ""
                continue

            follow = (
                transcript.max_scroll_y - transcript.scroll_y
                <= AUTO_SCROLL_RESUME_LINES
            )

            if classes == "tool" and SHELL_COMMAND_SEPARATOR in text:
                await _stop_answer_stream(answer_stream)
                answer_stream, block, raw_text = None, None, ""
                await transcript.mount(*_render_blocks(text, classes))
                if follow:
                    transcript.anchor()
                continue

            if block is None or is_new:
                await _stop_answer_stream(answer_stream)
                answer_stream, raw_text = None, ""
                block = Markdown("", classes=classes)
                await transcript.mount(block)
                if classes == "answer":
                    answer_stream = Markdown.get_stream(block)

            raw_text = await _write_stream_chunk(
                block,
                classes,
                text,
                raw_text,
                answer_stream,
            )
            if follow:
                transcript.anchor()

        await _stop_answer_stream(answer_stream)

    async def submit_message(self, message_item: MessageItem):
        # render user message
        transcript = self.query_one("#transcript", VerticalScroll)
        preview_text, preview_classes = get_item_text(message_item)  # type: ignore
        await transcript.mount(*_render_blocks(preview_text, preview_classes))
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
            # comment: finishing a reply while Review is active should not steal focus from that tab.
            if self.tabs().active == "chat-tab":
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
        self.slash_matches: list[str] = []

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
                workspace = self.app.workspace
                self.app.session = sessions.get_session(
                    chat_key=self.app.session[0],
                    session_id=str(uuid4()),
                    workspace=workspace,
                )
                self.app.workspace = workspace
                await self.app.load_messages()
                await self.app.queue().refresh_queue()
                return True
            case _:
                return False

    def update_slash_commands(self) -> None:
        option_list = self.app.query_one("#slash-commands", OptionList)
        row, column = self.cursor_location
        prefix = str(self.get_line(row))[:column].lstrip()
        query = prefix.split(maxsplit=1)[0] if prefix.startswith("/") else ""
        if not query or prefix != query:
            self.slash_matches = []
            option_list.clear_options()
            option_list.display = False
            return
        self.slash_matches = [
            command for command in SLASH_COMMANDS if command.startswith(query)
        ]
        option_list.clear_options()
        option_list.display = bool(self.slash_matches)
        if not self.slash_matches:
            return
        option_list.add_options(
            Option(f"{command} — {SLASH_COMMANDS[command]}")
            for command in self.slash_matches
        )
        option_list.highlighted = 0

    def apply_selected_slash_command(self, index: int | None = None) -> bool:
        if not self.slash_matches:
            return False
        option_list = self.app.query_one("#slash-commands", OptionList)
        match_index = option_list.highlighted if index is None else index
        if match_index is None or not (0 <= match_index < len(self.slash_matches)):
            return False
        command = self.slash_matches[match_index]
        lines = self.text.split("\n") or [""]
        row, _column = self.cursor_location
        line = lines[row]
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        lines[row] = f"{indent}{command}" if stripped.startswith("/") else command
        self.load_text("\n".join(lines))
        self.move_cursor((row, len(lines[row])))
        self.update_slash_commands()
        return True

    def on_text_area_changed(
        self,
        _event: TextArea.Changed | TextArea.SelectionChanged,
    ) -> None:
        self.update_slash_commands()

    on_text_area_selection_changed = on_text_area_changed

    def on_key(self, event: events.Key) -> None:
        if event.key not in {"up", "down"} or not self.slash_matches:
            return
        option_list = self.app.query_one("#slash-commands", OptionList)
        if not option_list.display:
            return
        event.stop()
        event.prevent_default()
        if event.key == "up":
            option_list.action_cursor_up()
        else:
            option_list.action_cursor_down()

    async def action_composer_enter(self) -> None:
        row, column = self.cursor_location
        prefix = str(self.get_line(row))[:column].lstrip()
        query = prefix.split(maxsplit=1)[0] if prefix.startswith("/") else ""
        if (
            query
            and prefix == query
            and self.slash_matches
            and not (self.text.strip() == query and query in SLASH_COMMANDS)
            and self.apply_selected_slash_command()
        ):
            return
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
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {package_version('faltoobot')}",
    )
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
