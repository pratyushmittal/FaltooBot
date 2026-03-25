from pathlib import Path
from typing import Callable, Generic, TypeAlias, TypeVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

T = TypeVar("T")
ItemSource: TypeAlias = list[T] | Callable[[str], list[T]]
MAX_RESULTS = 200


class Telescope(ModalScreen[T | None], Generic[T]):
    BINDINGS = [
        Binding("escape", "cancel", priority=True, show=False),
        Binding("down", "highlight_next", priority=True, show=False),
        Binding("up", "highlight_previous", priority=True, show=False),
    ]

    DEFAULT_CSS = """
    Telescope {
        align: center middle;
    }

    #telescope-dialog {
        width: 96;
        max-width: 96;
        height: auto;
        max-height: 24;
        padding: 1 2;
        background: $surface;
        border: round $primary;
    }

    #telescope-input {
        width: 1fr;
        margin-bottom: 1;
    }

    #telescope-options {
        width: 1fr;
        height: auto;
        max-height: 16;
    }
    """

    def __init__(
        self,
        *,
        items: ItemSource[T],
        title: str,
        placeholder: str,
    ) -> None:
        super().__init__()
        self.item_source = items
        self.dialog_title = title
        self.placeholder = placeholder
        self.results: list[T] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="telescope-dialog"):
            yield Static(self.dialog_title)
            yield Input(placeholder=self.placeholder, id="telescope-input")
            yield OptionList(id="telescope-options", markup=False)

    def on_mount(self) -> None:
        self._refresh_options("")
        self.query_one("#telescope-input", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_highlight_next(self) -> None:
        self.query_one("#telescope-options", OptionList).action_cursor_down()

    def action_highlight_previous(self) -> None:
        self.query_one("#telescope-options", OptionList).action_cursor_up()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh_options(event.value)

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        if not self.results:
            self.dismiss(None)
            return
        option_list = self.query_one("#telescope-options", OptionList)
        index = 0 if option_list.highlighted is None else option_list.highlighted
        self.dismiss(self.results[index])

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.results[event.option_index])

    def _refresh_options(self, query: str) -> None:
        if callable(self.item_source):
            load_items = cast(Callable[[str], list[T]], self.item_source)
            self.results = load_items(query)
        else:
            self.results = _filter_items(self.item_source, query)
        option_list = self.query_one("#telescope-options", OptionList)
        option_list.clear_options()
        option_list.add_options(Option(_item_label(result)) for result in self.results)
        if self.results:
            option_list.highlighted = 0


def _filter_items(items: list[T], query: str) -> list[T]:
    needle = query.strip().lower()
    if not needle:
        return items[:MAX_RESULTS]

    matches: list[tuple[int, T]] = []
    for item in items:
        score = _fuzzy_score(needle, _item_search_text(item))
        if score is None:
            continue
        matches.append((score, item))
    matches.sort(key=lambda item: (-item[0], _item_search_text(item[1])))
    return [item for _score, item in matches[:MAX_RESULTS]]


def _item_label(item: object) -> str:
    if isinstance(item, Path):
        return str(item)
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        mapping = cast(dict[str, object], item)
        if "title" in mapping:
            return str(mapping["title"])
        if "name" in mapping:
            return str(mapping["name"])
    return str(item)


def _item_search_text(item: object) -> str:
    if isinstance(item, Path):
        return str(item)
    return _item_label(item)


def _fuzzy_score(needle: str, haystack: str) -> int | None:
    text = haystack.lower()
    basename = Path(haystack).name.lower()
    if needle == basename:
        return 10_000 - len(text)
    if needle == text:
        return 9_000 - len(text)

    position = -1
    score = 0
    for character in needle:
        position = text.find(character, position + 1)
        if position == -1:
            return None
        score += 10
        if position > 0 and text[position - 1] in {"/", "_", "-"}:
            score += 4
    if basename.startswith(needle):
        score += 400
    elif needle in basename:
        score += 250
    elif text.startswith(needle):
        score += 100
    elif needle in text:
        score += 50
    return score - len(text)
