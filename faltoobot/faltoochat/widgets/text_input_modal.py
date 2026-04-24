from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class TextInputModal(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", priority=True, show=False)]

    DEFAULT_CSS = """
    TextInputModal {
        align: center middle;
    }

    #text-input-dialog {
        width: 80;
        max-width: 80;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: round $primary;
    }

    #text-input-input {
        width: 1fr;
    }
    """

    def __init__(
        self,
        *,
        initial_value: str = "",
        title: str = "Search review",
        placeholder: str = "Enter search term",
        allow_empty: bool = False,
    ) -> None:
        super().__init__()
        self.initial_value = initial_value
        self.title = title
        self.placeholder = placeholder
        self.allow_empty = allow_empty

    def compose(self) -> ComposeResult:
        with Vertical(id="text-input-dialog"):
            yield Static(self.title)
            yield Input(
                self.initial_value,
                placeholder=self.placeholder,
                id="text-input-input",
            )

    def on_mount(self) -> None:
        self.query_one("#text-input-input", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        term = event.value.strip()
        if not term and not self.allow_empty:
            self.dismiss(None)
            return
        self.dismiss(term)
