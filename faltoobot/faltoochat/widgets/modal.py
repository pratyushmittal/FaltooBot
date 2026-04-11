from collections import defaultdict
from dataclasses import replace
from typing import TYPE_CHECKING

from rich.console import RenderableType
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from faltoobot.config import app_root

if TYPE_CHECKING:
    from faltoobot.faltoochat.app import FaltooChatApp


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

    #text-modal-subheading {
        width: 1fr;
        margin: 0 0 1 0;
        color: $text-muted;
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
    subheading_id = "text-modal-subheading"

    def __init__(
        self,
        content: RenderableType,
        subheading: RenderableType | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> None:
        super().__init__(classes=f"-{self.variant}")
        self.content = content
        self.subheading = subheading
        self.width = width
        self.height = height

    def compose(self) -> ComposeResult:
        with Vertical(id=self.dialog_id):
            yield Static(self.modal_title, id=self.title_id)
            if self.subheading is not None:
                yield Static(self.subheading, id=self.subheading_id)
            with VerticalScroll(id=self.scroll_id):
                yield Static(self.content, id=self.content_id)
            yield Static(self.help_text, id=self.help_id)

    def on_mount(self) -> None:
        dialog = self.query_one(f"#{self.dialog_id}")
        if self.width is not None:
            dialog.styles.width = self.width
            dialog.styles.max_width = self.width
        if self.height is not None:
            dialog.styles.height = self.height
            dialog.styles.max_height = self.height

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
    scroll_id = "bindings-error-scroll"
    content_id = "bindings-error-message"
    help_id = "bindings-error-help"
    subheading_id = "bindings-error-subheading"

    def __init__(self, errors: list[str]) -> None:
        super().__init__("\n".join(errors), subheading=_keybindings_subheading())

    DEFAULT_CSS = TextModal.DEFAULT_CSS + """
    #bindings-error-dialog {
        width: 80;
        max-width: 80;
        height: 24;
        max-height: 24;
        padding: 1 2;
        background: $surface;
        border: round $error;
    }

    #bindings-error-title {
        width: 1fr;
        margin: 0 0 1 0;
        text-style: bold;
        color: $error;
        content-align: center middle;
    }

    #bindings-error-scroll {
        width: 1fr;
        height: 1fr;
        border: round $panel;
        padding: 1;
    }

    #bindings-error-message {
        width: 1fr;
    }

    #bindings-error-subheading {
        width: 1fr;
        margin: 0 0 1 0;
        color: $text-muted;
    }

    #bindings-error-help {
        width: 1fr;
        margin: 1 0 0 0;
        color: $text-muted;
        content-align: right middle;
    }
    """

    def on_mount(self) -> None:
        super().on_mount()
        self.query_one(f"#{self.content_id}", Static).update(
            _highlight_bindings_errors(
                str(self.content),
                self.app.current_theme.primary,
                self.app.current_theme.secondary or self.app.current_theme.primary,
            )
        )


class KeybindingsModal(TextModal):
    modal_title = "Keybindings"
    dialog_id = "keybindings-dialog"
    title_id = "keybindings-title"
    scroll_id = "keybindings-scroll"
    content_id = "keybindings-content"
    help_id = "keybindings-help"
    subheading_id = "keybindings-subheading"

    DEFAULT_CSS = TextModal.DEFAULT_CSS + """
    #keybindings-dialog {
        width: 80;
        max-width: 80;
        height: 20;
        max-height: 20;
        padding: 1 2;
        background: $surface;
        border: round $primary;
    }

    #keybindings-title {
        width: 1fr;
        margin: 0 0 1 0;
        text-style: bold;
        content-align: center middle;
    }

    #keybindings-scroll {
        width: 1fr;
        height: 1fr;
        border: round $panel;
        padding: 1;
    }

    #keybindings-content {
        width: 1fr;
        content-align: center top;
    }

    #keybindings-subheading {
        width: 1fr;
        margin: 0 0 1 0;
        color: $text-muted;
    }

    #keybindings-help {
        width: 1fr;
        margin: 1 0 0 0;
        color: $text-muted;
        content-align: right middle;
    }
    """

    @classmethod
    def from_screen(cls, app: "FaltooChatApp", screen) -> "KeybindingsModal":
        return cls(
            _render_keybindings(app, screen),
            subheading=_keybindings_subheading(),
            height=24,
        )


def _render_keybindings(app: "FaltooChatApp", screen) -> Text:
    descriptions = {
        binding.action: binding.description
        for bindings in app._keybindings.values()
        for binding in bindings
        if binding.description
    }
    grouped: defaultdict[str, list[str]] = defaultdict(list)
    for _key, (_node, binding, _enabled, _tooltip) in sorted(screen.active_bindings.items()):
        if binding.action not in descriptions:
            continue
        for key in binding.key.split(","):
            key_display = app.get_key_display(replace(binding, key=key))
            if key_display not in grouped[binding.action]:
                grouped[binding.action].append(key_display)
    rows = [
        (", ".join(keys), descriptions[action])
        for action, keys in grouped.items()
        if keys
    ]
    rows.sort(key=lambda row: (row[0].lower(), row[1].lower()))
    table_width = 60
    key_width = max(len("Key"), *(len(key) for key, _ in rows)) if rows else len("Key")
    key_width = min(key_width, 18)
    description_width = max(
        len("Description"),
        table_width - key_width - 7,
    )
    lines = [
        f"┌{'─' * (key_width + 2)}┬{'─' * (description_width + 2)}┐",
        f"│ {'Key':<{key_width}} │ {'Description':<{description_width}} │",
        f"├{'─' * (key_width + 2)}┼{'─' * (description_width + 2)}┤",
    ]
    lines.extend(
        f"│ {key:<{key_width}} │ {description:<{description_width}} │"
        for key, description in rows
    )
    lines.append(f"└{'─' * (key_width + 2)}┴{'─' * (description_width + 2)}┘")
    return Text("\n".join(lines))


def _keybindings_subheading() -> Text:
    path = app_root() / "bindings.toml"
    path_text = str(path)
    text = Text("Edit keybindings: ")
    start = len(text)
    text.append(path_text)
    text.stylize("dim", start, start + len(path_text))
    text.stylize(f"link file://{path}", start, start + len(path_text))
    return text


def _highlight_bindings_errors(content: str, action_color: str, key_color: str) -> Text:
    text = Text()
    highlight_index = 0
    while content:
        start = content.find("[")
        end = content.find("]", start + 1) if start != -1 else -1
        if start == -1 or end == -1:
            text.append(content)
            break
        text.append(content[:start])
        segment = content[start : end + 1]
        color = action_color if highlight_index % 2 == 0 else key_color
        text.append(segment, style=f"bold {color}")
        content = content[end + 1 :]
        highlight_index += 1
    return text
