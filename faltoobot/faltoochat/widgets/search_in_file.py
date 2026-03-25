from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class SearchInFile(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", priority=True, show=False)]

    DEFAULT_CSS = """
    SearchInFile {
        align: center middle;
    }

    #review-search-dialog {
        width: 80;
        max-width: 80;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: round $primary;
    }

    #review-search-input {
        width: 1fr;
    }
    """

    def __init__(self, *, initial_term: str = "") -> None:
        super().__init__()
        self.initial_term = initial_term

    def compose(self) -> ComposeResult:
        with Vertical(id="review-search-dialog"):
            yield Static("Search review")
            yield Input(
                self.initial_term,
                placeholder="Enter search term",
                id="review-search-input",
            )

    def on_mount(self) -> None:
        self.query_one("#review-search-input", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        term = event.value.strip()
        if not term:
            self.dismiss(None)
            return
        self.dismiss(term)
