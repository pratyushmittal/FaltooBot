from pathlib import Path
from typing import Any

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.message import Message
from textual.widgets import Markdown as TextualMarkdown
from textual.widgets import Static, TextArea
from textual.widgets.markdown import MarkdownFence

from faltoobot.store import QueuedPrompt, Session

from .entries import Entry, entry_class, queue_preview, uses_markdown, visible_content
from .images import image_markdown, paste_image_text, save_clipboard_image

MarkdownFence.highlight = classmethod(lambda cls, code, language: Content(code))  # type: ignore[assignment]


class Composer(TextArea):
    class Submitted(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    def workspace(self) -> Path:
        runtime = getattr(self.app, "runtime", None)
        session = getattr(runtime, "session", None)
        return session.workspace if isinstance(session, Session) else Path.cwd()

    def insert_text(self, value: str) -> None:
        if result := self._replace_via_keyboard(value, *self.selection):
            self.move_cursor(result.end_location)
            self.focus()

    async def _on_paste(self, event: events.Paste) -> None:
        if self.read_only:
            return
        event.stop()
        event.prevent_default()
        if getattr(self, "_skip_next_paste", False):
            self._skip_next_paste = False
            return
        self.insert_text(paste_image_text(event.text, self.workspace()))

    def action_paste(self) -> None:
        if self.read_only:
            return
        runtime = getattr(self.app, "runtime", None)
        session = getattr(runtime, "session", None)
        if isinstance(session, Session) and (path := save_clipboard_image(session)):
            self._skip_next_paste = True
            self.insert_text(image_markdown(path))

    def on_key(self, event: Any) -> None:
        handler = getattr(self.app, "handle_composer_key", None)
        if callable(handler) and handler(event.key):
            event.prevent_default()
            event.stop()
            return
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.post_message(self.Submitted(self.text))
            return
        if event.key == "tab":
            event.prevent_default()
            event.stop()
            self.insert("\t")
            return
        if event.key in {"shift+enter", "ctrl+j"}:
            event.prevent_default()
            event.stop()
            self.insert("\n")


class SlashCommandItem(Horizontal):
    class Picked(Message):
        def __init__(self, command: str) -> None:
            self.command = command
            super().__init__()

    def __init__(self, command: str, detail: str) -> None:
        self.command = command
        self.detail = detail
        super().__init__(classes="slash-command-item")

    def compose(self) -> ComposeResult:
        yield Static(Text(self.command), classes="slash-command-name")
        yield Static(Text(self.detail), classes="slash-command-detail")

    def on_click(self, event: Any) -> None:
        event.stop()
        self.post_message(self.Picked(self.command))


class QueueItem(Horizontal):
    class Picked(Message):
        def __init__(self, index: int) -> None:
            self.index = index
            super().__init__()

    def __init__(self, index: int, prompt: QueuedPrompt) -> None:
        self.index = index
        self.content = queue_preview(prompt.content)
        self.paused = prompt.paused
        super().__init__(classes="queue-item")

    def marker(self) -> str:
        return "□" if self.paused else "☑︎"

    def select(self, selected: bool) -> None:
        self.set_class(selected, "-selected")

    def compose(self) -> ComposeResult:
        yield Static(
            Text(f"{self.marker()} {self.content}", overflow="ellipsis", no_wrap=True),
            classes="queue-text",
        )

    def on_click(self, event: Any) -> None:
        event.stop()
        self.post_message(self.Picked(self.index))


class EntryBlock(Vertical):
    DEFAULT_CSS = """
    EntryBlock,
    LiveMarkdownBlock {
        width: 1fr;
        max-width: 80;
        min-width: 0;
        height: auto;
        margin: 0 0 1 0;
    }

    EntryBlock > .body,
    LiveMarkdownBlock > .body {
        width: 1fr;
        min-width: 0;
        height: auto;
        padding: 0 1;
        background: transparent;
        color: $text;
        overflow-x: hidden;
    }

    EntryBlock.entry-you > .body,
    LiveMarkdownBlock.entry-you > .body {
        background: $primary 8%;
    }

    EntryBlock.entry-bot > .body,
    LiveMarkdownBlock.entry-bot > .body {
        background: $surface;
    }

    EntryBlock.entry-thinking > .body,
    LiveMarkdownBlock.entry-thinking > .body {
        color: $text-muted;
        background: $surface;
    }

    EntryBlock.entry-tool > .body,
    LiveMarkdownBlock.entry-tool > .body {
        color: $secondary;
        background: $secondary 8%;
    }

    EntryBlock.entry-error > .body,
    LiveMarkdownBlock.entry-error > .body {
        color: $error;
        background: $error 8%;
    }

    EntryBlock.entry-opened > .body,
    LiveMarkdownBlock.entry-opened > .body {
        color: $accent;
        background: $accent 8%;
    }

    EntryBlock.entry-banner > .body,
    EntryBlock.entry-meta > .body,
    LiveMarkdownBlock.entry-banner > .body,
    LiveMarkdownBlock.entry-meta > .body {
        background: transparent;
    }

    EntryBlock.entry-banner > .body,
    LiveMarkdownBlock.entry-banner > .body {
        color: $warning;
        text-style: bold;
    }

    EntryBlock.entry-meta > .body,
    LiveMarkdownBlock.entry-meta > .body {
        color: $text-disabled;
    }
    """

    def __init__(self, entry: Entry) -> None:
        self.entry = entry
        super().__init__(classes=entry_class(entry.kind))

    def compose(self) -> ComposeResult:
        kind = self.entry.kind
        content = visible_content(self.entry.kind, self.entry.content)
        if kind in {"banner", "meta"} or not self.uses_markdown():
            yield Static(Text(content), id="body", classes="body")
            return
        yield TextualMarkdown(content, id="body", classes="body")

    def uses_markdown(self) -> bool:
        return uses_markdown(self.entry.kind, self.entry.content)

    def same_layout(self, entry: Entry) -> bool:
        return (
            self.entry.kind == entry.kind
            and self.uses_markdown() == uses_markdown(entry.kind, entry.content)
            and ("\n" in self.entry.content) == ("\n" in entry.content)
        )

    def set_entry(self, entry: Entry) -> bool:
        if not self.same_layout(entry):
            return False
        self.entry = entry
        if self.uses_markdown():
            self.query_one("#body", TextualMarkdown).update(
                visible_content(entry.kind, entry.content)
            )
            return True
        self.query_one("#body", Static).update(Text(visible_content(entry.kind, entry.content)))
        return True

    def on_resize(self, _: events.Resize) -> None:
        app = self.app
        if getattr(app, "follow_transcript", False):
            app.scroll_transcript_end_once()  # type: ignore[attr-defined]


class LiveMarkdownBlock(Vertical):
    DEFAULT_CSS = EntryBlock.DEFAULT_CSS

    def __init__(self, entry: Entry) -> None:
        self.entry = entry
        super().__init__(classes=entry_class(entry.kind))

    def compose(self) -> ComposeResult:
        yield Static(
            Text(visible_content(self.entry.kind, self.entry.content)), id="body", classes="body"
        )

    def set_entry(self, entry: Entry) -> bool:
        if entry.kind != self.entry.kind or entry.kind not in {"bot", "thinking"}:
            return False
        self.entry = entry
        self.query_one("#body", Static).update(Text(visible_content(entry.kind, entry.content)))
        return True

    def on_resize(self, _: events.Resize) -> None:
        app = self.app
        if getattr(app, "follow_transcript", False):
            app.scroll_transcript_end_once()  # type: ignore[attr-defined]
