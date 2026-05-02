from typing import TYPE_CHECKING, cast

from textual import events
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Static

from faltoobot.gpt_utils import MessageItem
from faltoobot.session_utils import decompose_local_message_item
from .. import submit_queue
from ..messages_rendering import get_item_text

if TYPE_CHECKING:
    from faltoobot.faltoochat.app import Composer, FaltooChatApp

QUEUE_PREVIEW_CHARS = 72


class QueueItem(Static):
    def __init__(self, index: int, message_item: MessageItem) -> None:
        self.index = index
        self.message_item = message_item
        super().__init__(classes="queue-item")
        self.update_text()

    def update_text(self) -> None:
        auto_submit = bool(self.message_item.get("auto_submit"))
        marker = "☑︎" if auto_submit else "☐"
        if not (rendering := get_item_text(self.message_item)):
            preview = ""
        else:
            preview = " ".join(
                part.strip() for part in rendering[0].splitlines() if part.strip()
            )
            preview = (preview or rendering[0].strip())[:QUEUE_PREVIEW_CHARS]
        self.update(f"{marker} {preview}")

    def select(self, selected: bool) -> None:
        self.set_class(selected, "-selected")


class QueueWidget(Vertical):
    app: "FaltooChatApp"

    can_focus = True

    BINDINGS = [
        Binding("up", "queue_previous", "Previous", priority=True),
        Binding("down", "queue_next", "Next", priority=True),
        Binding("enter", "edit_selected", "Edit", priority=True),
        Binding("space", "toggle_auto_submit", "Toggle auto", priority=True),
        Binding("delete", "remove_selected", "Remove", priority=True),
        Binding("backspace", "remove_selected", "Remove", priority=True),
        Binding("shift+up", "move_selected_up", "Move up", priority=True),
        Binding("shift+down", "move_selected_down", "Move down", priority=True),
    ]

    DEFAULT_CSS = """
    QueueWidget {
        height: auto;
        max-height: 8;
        layout: vertical;
        background: $background;
        border: round $panel;
        border-title-align: left;
        border-title-color: $panel;
        border-title-background: $background;
        padding: 0;
        display: none;
    }

    QueueWidget:focus {
        border: round $primary;
        border-title-color: $primary;
    }

    QueueItem {
        width: 1fr;
        padding: 0 1;
        color: $text;
    }

    QueueItem.-selected {
        background: $primary 18%;
    }
    """

    def __init__(self) -> None:
        super().__init__(id="queue")
        self.border_title = "Queue"
        self.messages: list[MessageItem] = []
        self.selected = 0

    def selected_message_id(self) -> str | None:
        if not self.messages or self.selected >= len(self.messages):
            return None
        message_id = self.messages[self.selected].get("id")
        return message_id if isinstance(message_id, str) and message_id else None

    def normalize_selection(self) -> None:
        if not self.messages:
            self.selected = 0
            return
        self.selected = max(0, min(self.selected, len(self.messages) - 1))

    async def refresh_queue(self) -> None:
        self.messages = submit_queue.get_queue(self.app.session)
        self.normalize_selection()
        await self.remove_children()
        self.display = bool(self.messages)
        if not self.messages:
            return
        items = [
            QueueItem(index, message) for index, message in enumerate(self.messages)
        ]
        for item in items:
            item.select(item.index == self.selected)
        await self.mount(*items)

    async def add_to_queue(self, message_item: MessageItem) -> None:
        queue = submit_queue.add_to_queue(self.app.session, message_item)
        message_id = queue[-1].get("id")
        # comment: add_to_queue assigns ids, but older malformed payloads may still surprise us.
        if isinstance(message_id, str) and message_id:
            submit_queue.set_auto_submit(self.app.session, message_id)
        await self.refresh_queue()

    async def submit_next_message(self) -> None:
        for message_item in submit_queue.get_queue(self.app.session):
            if not message_item.get("auto_submit"):
                continue
            message_id = message_item.get("id")
            # comment: older or broken queue entries may be missing ids. Remove them so one bad
            # item doesn't block all later auto-submits forever.
            if not isinstance(message_id, str) or not message_id:
                continue
            submit_queue.remove_from_queue(self.app.session, message_id)
            await self.refresh_queue()
            await self.app.handle_message(message_item)
            return

    def select(self, index: int) -> None:
        self.selected = index
        self.normalize_selection()
        for child in self.children:
            if isinstance(child, QueueItem):
                child.select(child.index == self.selected)

    def move_selection(self, delta: int) -> None:
        if not self.messages:
            return
        self.select(self.selected + delta)

    def action_queue_previous(self) -> None:
        self.move_selection(-1)

    def action_queue_next(self) -> None:
        self.move_selection(1)

    async def action_toggle_auto_submit(self) -> None:
        message_id = self.selected_message_id()
        # comment: hidden or empty queues can still briefly receive key presses during refresh.
        if message_id is None:
            return
        if self.messages[self.selected].get("auto_submit"):
            submit_queue.remove_auto_submit(self.app.session, message_id)
        else:
            submit_queue.set_auto_submit(self.app.session, message_id)
        await self.refresh_queue()
        if not self.app.is_answering:
            await self.submit_next_message()

    async def action_remove_selected(self) -> None:
        message_id = self.selected_message_id()
        # comment: hidden or empty queues can still briefly receive key presses during refresh.
        if message_id is None:
            return
        submit_queue.remove_from_queue(self.app.session, message_id)
        await self.refresh_queue()

    async def action_edit_selected(self) -> None:
        message_id = self.selected_message_id()
        # comment: hidden or empty queues can still briefly receive key presses during refresh.
        if message_id is None:
            return
        message_item = self.messages[self.selected]
        submit_queue.remove_from_queue(self.app.session, message_id)
        await self.refresh_queue()

        composer = cast("Composer", self.app.query_one("#composer"))
        question, attachments = decompose_local_message_item(message_item)
        composer.load_text(question)
        last_row = composer.document.line_count - 1
        composer.move_cursor((last_row, len(composer.document.get_line(last_row))))
        composer.set_attachments(list(attachments))
        self.app.focus_composer()

    async def action_move_selected_up(self) -> None:
        message_id = self.selected_message_id()
        # comment: hidden or empty queues can still briefly receive key presses during refresh.
        if message_id is None or self.selected == 0:
            return
        self.selected -= 1
        submit_queue.move_up(self.app.session, message_id)
        await self.refresh_queue()

    async def action_move_selected_down(self) -> None:
        message_id = self.selected_message_id()
        # comment: hidden or empty queues can still briefly receive key presses during refresh.
        if message_id is None or self.selected >= len(self.messages) - 1:
            return
        self.selected += 1
        submit_queue.move_down(self.app.session, message_id)
        await self.refresh_queue()

    def on_queue_item_click(self, event: events.Click) -> None:
        if isinstance(event.widget, QueueItem):
            self.select(event.widget.index)
