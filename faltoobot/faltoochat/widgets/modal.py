
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static


class TextModal(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss_modal", priority=True, show=False)]

    DEFAULT_CSS = """
    TextModal {
        align: center middle;
    }

    #text-modal-dialog {
        width: 96;
        max-width: 96;
        height: 24;
        padding: 1 2;
        background: $surface;
        border: round $primary;
    }

    TextModal.-error #text-modal-dialog {
        width: 72;
        max-width: 72;
        height: auto;
        border: round $error;
    }

    #text-modal-title {
        text-style: bold;
        margin: 0 0 1 0;
    }

    TextModal.-error #text-modal-title {
        color: $error;
    }

    #text-modal-scroll {
        width: 1fr;
        height: 1fr;
        border: round $panel;
        padding: 0 1;
    }

    TextModal.-error #text-modal-scroll {
        height: auto;
        border: none;
        padding: 0;
    }

    #text-modal-content {
        width: 1fr;
    }

    #text-modal-help {
        margin: 1 0 0 0;
        color: $text-muted;
    }
    """

    modal_title = ""
    help_text = "Press Esc to close."
    variant = "primary"
    dismiss_on_any_key = False
    dialog_id = "text-modal-dialog"
    title_id = "text-modal-title"
    scroll_id = "text-modal-scroll"
    content_id = "text-modal-content"
    help_id = "text-modal-help"

    def __init__(self, content: str) -> None:
        super().__init__(classes=f"-{self.variant}")
        self.content = content

    def compose(self) -> ComposeResult:
        with Vertical(id=self.dialog_id):
            yield Static(self.modal_title, id=self.title_id)
            with VerticalScroll(id=self.scroll_id):
                yield Static(Text(self.content), id=self.content_id)
            yield Static(self.help_text, id=self.help_id)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        if not self.dismiss_on_any_key:
            return
        event.stop()
        self.dismiss(None)


class BindingsErrorModal(TextModal):
    modal_title = "Bindings config error"
    help_text = "Press any key to dismiss."
    variant = "error"
    dismiss_on_any_key = True
    dialog_id = "bindings-error-dialog"
    title_id = "bindings-error-title"
    content_id = "bindings-error-message"
    help_id = "bindings-error-help"

