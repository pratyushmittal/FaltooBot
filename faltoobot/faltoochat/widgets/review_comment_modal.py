from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static, TextArea


class ReviewCommentEditor(TextArea):
    BINDINGS = [
        Binding("enter", "submit_comment", priority=True, show=False),
        Binding("shift+enter", "insert_newline", priority=True, show=False),
    ]

    def action_submit_comment(self) -> None:
        modal = self.screen
        if not isinstance(modal, ReviewCommentModal):
            return
        modal.submit_comment(self.text)

    def action_insert_newline(self) -> None:
        self.insert("\n")


class ReviewCommentModal(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", priority=True, show=False)]

    DEFAULT_CSS = """
    ReviewCommentModal {
        align: center middle;
    }

    #review-comment-dialog {
        width: 80;
        max-width: 80;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: round $primary;
    }

    #review-comment-code {
        margin: 1 0;
        color: $text-muted;
    }

    #review-comment-input {
        width: 1fr;
        height: 5;
    }
    """

    def __init__(
        self,
        file_path: Path,
        line_number_start: int,
        line_number_end: int,
        code: str,
        *,
        initial_comment: str = "",
    ) -> None:
        super().__init__()
        self.file_path = file_path
        self.line_number_start = line_number_start
        self.line_number_end = line_number_end
        self.code = code
        self.initial_comment = initial_comment

    def compose(self) -> ComposeResult:
        with Vertical(id="review-comment-dialog"):
            yield Static(
                f"Add review for {self.file_path}:{self.line_number_start}-{self.line_number_end}"
            )
            yield Static(self.code, id="review-comment-code", markup=False)
            yield ReviewCommentEditor(
                self.initial_comment,
                id="review-comment-input",
                soft_wrap=True,
                show_line_numbers=False,
                highlight_cursor_line=False,
                placeholder="Enter review comment",
            )

    def on_mount(self) -> None:
        self.query_one("#review-comment-input", ReviewCommentEditor).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def submit_comment(self, comment: str) -> None:
        # comment: submitting a blank comment deletes an existing review and is ignored for new ones.
        self.dismiss(comment.strip())
