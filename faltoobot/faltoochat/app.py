import argparse
import asyncio
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from textual import events, getters
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Center, Vertical, VerticalScroll
from textual.widgets import (
    Footer,
    Markdown,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from faltoobot import notify_queue, sessions
from faltoobot.config import load_textual_theme, save_textual_theme
from faltoobot.faltoochat.terminal import textual_theme_from_terminal
from faltoobot.gpt_utils import MessageItem
from faltoobot.keybindings import apply_faltoochat_keybindings, load_keybindings
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
from .review import ReviewView, _syntax_highlight_theme
from .stream import get_event_text
from .widgets import (
    BindingsErrorModal,
    KeybindingsModal,
    QueueWidget,
    ReviewDiffView,
    SearchFile,
    SlashCommandsOptionList,
)

STARTUP_MESSAGES_LIMIT = 100
AUTO_SCROLL_RESUME_LINES = 3


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
    DEFAULT_BINDINGS = [
        Binding("ctrl+1", "show_chat_tab", "Chat Tab", priority=True, show=False),
        Binding("ctrl+2", "show_review_tab", "Review Tab", priority=True, show=False),
        Binding(
            "ctrl+r",
            "toggle_review_tab",
            "Toggle Review Tab",
            priority=True,
            show=False,
        ),
        Binding(
            "ctrl+p",
            "command_palette",
            "Command Palette",
            show=False,
            priority=True,
            tooltip="Open the command palette",
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
    ) -> None:
        self._keybindings, self._binding_errors = load_keybindings()
        apply_faltoochat_keybindings(self._keybindings)
        self._persist_theme_changes = False
        super().__init__()
        if (saved_theme := load_textual_theme()) in self.available_themes:
            self.theme = saved_theme
        elif theme := textual_theme_from_terminal():
            self.theme = theme
        self._persist_theme_changes = True
        self.session = session
        self.workspace = Path(sessions.get_messages(session)["workspace"])
        self.is_answering = False
        self._is_polling_notifications = False

    def get_system_commands(self, screen) -> Iterable[SystemCommand]:
        """Return commands shown in Textual's command palette (Ctrl+P) for the active screen."""
        yield from super().get_system_commands(screen)
        yield SystemCommand(
            "Keybindings",
            "Show all current keybindings",
            lambda: self.push_screen(KeybindingsModal.from_screen(self, screen)),
        )

    def queue(self) -> QueueWidget:
        return self.query_one(QueueWidget)

    def _watch_theme(self, theme_name: str) -> None:
        super()._watch_theme(theme_name)
        if not self._persist_theme_changes:
            return
        save_textual_theme(theme_name)
        syntax_theme = _syntax_highlight_theme(theme_name)
        for viewer in self.query(ReviewDiffView):
            # comment: a plain refresh won't switch the syntax palette because the
            # TextArea theme name is stored on the widget and only initialized once.
            viewer.theme = syntax_theme
            viewer.refresh()

    def tabs(self) -> TabbedContent:
        return self.query_one("#tabs", TabbedContent)

    def focus_composer(self) -> None:
        self.set_focus(self.query_one("#composer", Composer), scroll_visible=False)

    def action_show_chat_tab(self) -> None:
        self.tabs().active = "chat-tab"
        transcript = self.query_one("#transcript", VerticalScroll)
        self.call_after_refresh(transcript.scroll_end, animate=False)
        if self.screen.is_modal:
            # comment: focus inside the active modal instead of closing it.
            return
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
                                yield SlashCommandsOptionList(
                                    id="slash-commands", markup=False
                                )
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

    async def on_mount(self) -> None:
        self.query_one("#slash-commands", SlashCommandsOptionList).hide_commands()
        await self.load_messages(recent_limit=STARTUP_MESSAGES_LIMIT)
        await self.queue().refresh_queue()
        if self._binding_errors:
            self.push_screen(BindingsErrorModal(self._binding_errors))
        self.set_interval(1.0, self._poll_notifications)

    def _poll_notifications(self) -> None:
        # comment: timer ticks can overlap while an earlier notification drain is still running.
        if self._is_polling_notifications:
            return
        self._is_polling_notifications = True
        self.run_worker(self._drain_notifications(), exclusive=False)

    async def _drain_notifications(self) -> None:
        """Deliver queued notifications for this chat and ack or requeue them."""
        try:
            for path, notification in notify_queue.claim_notifications(
                lambda item: item["chat_key"] == self.session.chat_key
            ):
                try:
                    message_item = get_local_user_message_item(
                        notify_queue.format_notification_message(notification),
                        [],
                    )
                    await self.handle_message(message_item)
                    notify_queue.ack_notification(path)
                    self.notify("Received sub-agent response")
                except Exception:
                    notify_queue.requeue_notification(path)
                    raise
        finally:
            self._is_polling_notifications = False

    async def handle_message(self, message_item: MessageItem) -> None:
        # comment: queued messages should wait for the active answer to finish before starting a new turn.
        if self.is_answering:
            await self.queue().add_to_queue(message_item)
            return
        self.is_answering = True
        composer = self.query_one("#composer", Composer)
        composer.border_subtitle = "answering"
        # exclusive=True tells Textual to cancel all previous workers before starting the new one
        self.run_worker(self.submit_message(message_item), exclusive=True)

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

    async def show_local_answer(self, text: str) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        await transcript.mount(Markdown(text, classes="answer"))
        self.call_after_refresh(
            transcript.scroll_end,
            animate=False,
            immediate=True,
        )

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

        stored = await sessions.append_user_turn(
            self.session,
            question=question,
            attachments=attachments,
        )
        if not stored:
            return
        async for event in sessions.get_answer_streaming(self.session):
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

    async def submit_message(self, message_item: MessageItem) -> None:
        composer = self.query_one("#composer", Composer)
        try:
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
        Binding("@", "mention_file", "Mention File", priority=True, show=False),
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

    def on_text_area_changed(
        self,
        _event: TextArea.Changed | TextArea.SelectionChanged,
    ) -> None:
        # show slash commands if applicable
        self.app.query_one("#slash-commands", SlashCommandsOptionList).show_matches_for(
            self.text
        )

    on_text_area_selection_changed = on_text_area_changed

    def on_key(self, event: events.Key) -> None:
        option_list = self.app.query_one("#slash-commands", SlashCommandsOptionList)
        if event.key not in {"up", "down"} or not option_list.options:
            return
        if not option_list.display:
            return
        event.stop()
        event.prevent_default()
        if event.key == "up":
            option_list.action_cursor_up()
        else:
            option_list.action_cursor_down()

    async def action_composer_enter(self) -> None:
        option_list = self.app.query_one("#slash-commands", SlashCommandsOptionList)
        if command := option_list.selected_completion(self.text):
            self.clear()
            self.insert(command, maintain_selection_offset=False)
            return

        question = self.text.strip()
        attachments = self.take_attachments()
        if not question and not attachments:
            return

        if question.startswith("/") and await option_list.handle_text(
            question, attachments
        ):
            return

        self.load_text("")
        message_item = get_local_user_message_item(question, attachments)
        await self.app.handle_message(message_item)

    def action_newline(self) -> None:
        self.insert("\n")

    def action_mention_file(self) -> None:
        def on_result(result: Path | None) -> None:
            if result is None:
                return
            self.insert(f"`{result}` ")
            self.focus()

        self.app.push_screen(
            SearchFile(
                workspace=self.app.workspace,
                title="Mention file",
                placeholder="Type a filename or path",
            ),
            on_result,
        )


async def _run_one_shot(session: sessions.Session, prompt: str) -> str:
    stored = await sessions.append_user_turn(session, question=prompt)
    if not stored:
        return ""
    return await sessions.get_answer(session)


def _workspace_from_args(workspace: str | None) -> Path:
    base = Path.cwd() if workspace is None else Path(workspace).expanduser()
    path = base.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


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
    parser.add_argument("--workspace", help="workspace path to use for this chat")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace = _workspace_from_args(args.workspace)
    session_id = str(uuid4()) if args.new_session else None
    chat_key = sessions.get_dir_chat_key(workspace, is_sub_agent=bool(args.prompt))
    session = sessions.get_session(
        chat_key=chat_key,
        session_id=session_id,
        workspace=workspace,
    )
    try:
        if args.prompt:
            output = asyncio.run(_run_one_shot(session, args.prompt))
            if output:
                print(output)
            return 0
        FaltooChatApp(session=session).run()
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
