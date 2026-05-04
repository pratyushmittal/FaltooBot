from typing import Any, cast

from markdown_it import MarkdownIt
from pygments.token import Token
from rich.console import Console, ConsoleOptions, RenderResult
from rich.markdown import BlockQuote, Markdown as RichMarkdown
from rich.measure import Measurement
from rich.segment import Segment
from rich.style import Style
from rich.syntax import SyntaxTheme
from rich.text import Text
from rich.theme import Theme as RichTheme
from textual import events
from textual.color import Color
from textual.events import Resize
from textual.geometry import Offset, Size
from textual.selection import Selection
from textual.strip import Strip
from textual.widgets import RichLog
from textual.widgets.text_area import TextAreaTheme

from faltoobot.faltoochat.messages_rendering import SHELL_COMMAND_SEPARATOR

MAX_BLOCK_WIDTH = 80

_TOKEN_SCOPES = {
    Token.Comment: "comment",
    Token.Generic.Emph: "italic",
    Token.Generic.Heading: "heading",
    Token.Generic.Strong: "bold",
    Token.Keyword: "keyword",
    Token.Keyword.Constant: "keyword",
    Token.Keyword.Namespace: "keyword",
    Token.Keyword.Type: "type",
    Token.Literal.Number: "number",
    Token.Literal.String: "string",
    Token.Literal.String.Doc: "string.documentation",
    Token.Name.Attribute: "variable",
    Token.Name.Builtin: "type.builtin",
    Token.Name.Class: "class",
    Token.Name.Function: "function",
    Token.Name.Tag: "tag",
    Token.Name.Variable: "variable",
    Token.Operator: "operator",
}


def _text_area_theme(app_theme: str) -> TextAreaTheme:
    name = "github_light" if "light" in app_theme else "vscode_dark"
    return cast(TextAreaTheme, TextAreaTheme.get_builtin_theme(name))


class _TextAreaSyntaxTheme(SyntaxTheme):
    def __init__(self, app_theme: str, background_style: Style) -> None:
        theme = _text_area_theme(app_theme)
        self.base_style = theme.base_style or Style()
        self.syntax_styles = theme.syntax_styles
        self.background_style = background_style

    def get_style_for_token(self, token_type: Any) -> Style:
        while token_type is not None:
            if scope := _TOKEN_SCOPES.get(token_type):
                return self.syntax_styles.get(scope, self.base_style)
            token_type = token_type.parent
        return self.base_style

    def get_background_style(self) -> Style:
        return self.background_style


class _BlockQuote(BlockQuote):
    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        lines = console.render_lines(
            self.elements, options.update(width=options.max_width - 4), style=self.style
        )
        bar_style = console.get_style("markdown.block_quote_bar")
        for line in lines:
            yield Segment("▌ ", bar_style)
            yield from line
            yield Segment.line()


class _Markdown(RichMarkdown):
    elements = {**RichMarkdown.elements, "blockquote_open": _BlockQuote}


def _theme_styles(owner: RichLog) -> dict[str, Style]:
    colors = owner.app.current_theme.to_color_system().generate()
    background = Color.parse(colors["background"])

    def color(name: str):
        return Color.parse(colors[name]).rich_color

    def tint(name: str, amount: float):
        return background.blend(Color.parse(colors[name]), amount).rich_color

    text = Style(color=owner.styles.color.rich_color)
    return {
        "text": text,
        "muted": Style(color=color("foreground-muted")),
        "heading": Style(color=color("primary")),
        "link": Style(color=color("text-accent"), underline=True),
        "quote": text,
        "quote-bar": Style(color=color("primary")),
        "code": _text_area_theme(owner.app.theme).syntax_styles["inline_code"]
        + Style(bgcolor=tint("warning", 0.08)),
        "code-block": text + Style(bgcolor=tint("foreground", 0.04)),
        "user": text + Style(bgcolor=tint("primary", 0.15)),
        "user-border": Style(color=color("primary"), bgcolor=tint("primary", 0.15)),
        "tool": Style(color=color("foreground-muted"), bgcolor=tint("warning", 0.08)),
        "tool-summary": text + Style(bgcolor=tint("warning", 0.32)),
        "unknown": text + Style(bgcolor=tint("error", 0.12)),
        "unknown-border": Style(color=color("error"), bgcolor=tint("error", 0.12)),
    }


def _layout(
    classes: set[str], styles: dict[str, Style]
) -> tuple[Style, Style, bool, int, int]:
    if "history-summary" in classes or "thinking" in classes:
        return styles["muted"], Style.null(), False, 1, 1
    if "user" in classes:
        return styles["user"], styles["user-border"], True, 1, 1
    if "tool-summary" in classes:
        return styles["tool-summary"], Style.null(), False, 1, 0
    if "tool" in classes:
        return styles["tool"], Style.null(), False, 2, 1
    if "unknown" in classes:
        return styles["unknown"], styles["unknown-border"], True, 1, 1
    return styles["text"], Style.null(), False, 1, 1


def _message_blocks(text: str, classes: str) -> list[tuple[str, str]]:
    if classes != "tool" or SHELL_COMMAND_SEPARATOR not in text:
        return [(text, classes)]
    summary, command = text.split(SHELL_COMMAND_SEPARATOR, maxsplit=1)
    blocks = [(summary, "tool tool-summary")]
    if command.strip():
        blocks.append((command.strip(), "tool tool-command"))
    return blocks


def _selection_parts(text: str, classes: str) -> list[str]:
    if classes != "answer":
        return [text]
    lines = text.splitlines()
    parts = [
        "\n".join(lines[start:end])
        for token in MarkdownIt().parse(text)
        if token.level == 0
        and token.map is not None
        and not token.type.endswith("_close")
        for start, end in [token.map]
    ]
    return parts or [text]


class _TranscriptBlock:
    def __init__(self, text: str, classes: str, owner: RichLog) -> None:
        self.text = text
        self.classes = set(classes.split())
        self.owner = owner

    def __rich_measure__(
        self, _console: Console, options: ConsoleOptions
    ) -> Measurement:
        return Measurement(1, min(MAX_BLOCK_WIDTH, options.max_width))

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        styles = _theme_styles(self.owner)
        block_style, border_style, has_border, padding, margin_bottom = _layout(
            self.classes, styles
        )
        content_width = max(
            1, min(MAX_BLOCK_WIDTH, options.max_width) - int(has_border) - padding * 2
        )
        renderable = Text(self.text)
        if "history-summary" in self.classes or "unknown" in self.classes:
            renderable = Text.from_markup(self.text)
        elif "tool-command" not in self.classes:
            renderable = _Markdown(
                self.text,
                code_theme=_TextAreaSyntaxTheme(
                    self.owner.app.theme, styles["code-block"]
                ),  # type: ignore
            )
        with console.use_theme(
            RichTheme(
                {
                    "markdown.code": styles["code"],
                    "markdown.code_inline": styles["code"],
                    "markdown.code_block": styles["code-block"],
                    "markdown.block_quote": styles["quote"],
                    "markdown.block_quote_bar": styles["quote-bar"],
                    "markdown.h1": styles["heading"],
                    "markdown.h2": styles["heading"],
                    "markdown.h3": styles["heading"],
                    "markdown.h4": styles["heading"],
                    "markdown.h5": styles["heading"],
                    "markdown.h6": styles["heading"],
                    "markdown.link": styles["link"],
                }
            )
        ):
            lines = list(
                Segment.split_lines(
                    console.render(renderable, options.update_width(content_width))
                )
            ) or [[]]
        if "tool-command" in self.classes:
            tool_command_max_lines = 3
            lines = lines[:tool_command_max_lines]
        if "review-comments" in self.classes:
            lines = [[]] + lines + [[]]

        for line in lines:
            line = Segment.adjust_line_length(
                list(Segment.apply_style(line, style=block_style)),
                content_width,
                block_style,
            )
            if has_border:
                yield Segment("┃", border_style)
            yield Segment(" " * padding, block_style)
            yield from line
            yield Segment(" " * padding, block_style)
            yield Segment.line()
        for _ in range(margin_bottom):
            yield Segment.line()


class TranscriptLog(RichLog):
    ALLOW_SELECT = True
    can_focus = False
    FOCUS_ON_CLICK = False

    def __init__(self, **kwargs: Any) -> None:
        self.messages: list[tuple[str, str]] = []
        self.message_ranges: list[tuple[str, int, int]] = []
        self.selection_ranges: list[tuple[str, int, int]] = []
        self._last_click_line: int | None = None
        super().__init__(wrap=True, markup=False, **kwargs)

    def notify_style_update(self) -> None:
        super().notify_style_update()
        if self._size_known:
            self._render_messages()

    def on_resize(self, event: Resize) -> None:
        was_size_known = self._size_known
        super().on_resize(event)
        if event.size.width and not was_size_known:
            self._render_messages()

    def write_entry(self, text: str, classes: str, *, scroll_end: bool = True) -> int:
        blocks = _message_blocks(text, classes)
        for index, (block_text, block_classes) in enumerate(blocks):
            self.write_message(
                block_text,
                block_classes,
                scroll_end=scroll_end and index == len(blocks) - 1,
            )
        return len(blocks)

    def write_message(
        self, text: str, classes: str, *, scroll_end: bool = True
    ) -> None:
        self.messages.append((text, classes))
        if self._size_known:
            self._write_message(text, classes, scroll_end=scroll_end)

    def _clear_selection(self) -> None:
        self.screen.selections.pop(self, None)

    def _write_message(
        self, text: str, classes: str, *, scroll_end: bool = True
    ) -> None:
        self._clear_selection()
        start = len(self.lines)
        parts = _selection_parts(text, classes)
        last_index = len(parts) - 1
        for index, part in enumerate(parts):
            part_start = len(self.lines)
            self.write(
                _TranscriptBlock(part, classes, self),
                scroll_end=scroll_end and index == last_index,
            )
            self.selection_ranges.append((classes, part_start, len(self.lines)))
        self.message_ranges.append((classes, start, len(self.lines)))
        self.refresh()

    def _render_messages(self) -> None:
        messages = self.messages
        self.messages = []
        self.clear_messages()
        for text, classes in messages:
            self.write_message(text, classes, scroll_end=False)

    def replace_last_message(self, text: str, classes: str) -> None:
        if self.messages:
            self.pop_message()
        self.write_message(text, classes)

    def pop_message(self) -> None:
        self._clear_selection()
        _classes, start, end = self.message_ranges.pop()
        self.messages.pop()
        while self.selection_ranges and self.selection_ranges[-1][1] >= start:
            self.selection_ranges.pop()
        del self.lines[start:end]
        self._line_cache.clear()
        self._widest_line_width = max(
            (line.cell_length for line in self.lines), default=0
        )
        self.virtual_size = Size(self._widest_line_width, len(self.lines))
        self.refresh()

    def clear_messages(self) -> None:
        self._clear_selection()
        self.messages.clear()
        self.message_ranges.clear()
        self.selection_ranges.clear()
        self.clear()

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if offset := event.get_content_offset(self):
            self._last_click_line = self.scroll_offset[1] + offset.y

    def text_select_all(self) -> None:
        if self._last_click_line is not None:
            self._select_block_at_line(self._last_click_line)

    def on_click(self, event: events.Click) -> None:
        event.prevent_default()
        offset = event.get_content_offset(self)
        if offset is not None:
            self._select_block_at_line(self.scroll_offset[1] + offset.y)
        event.stop()

    def _select_block_at_line(self, line_index: int) -> None:
        start = self._selection_range_for_line(line_index)
        if start is None:
            return
        _classes, start, end = self.selection_ranges[start]
        last_line = max(start, end - 1)
        while last_line > start and not self.lines[last_line].text.rstrip():
            last_line -= 1
        start_text = self.lines[start].text
        start_x = (
            2
            if start_text.startswith("┃ ")
            else len(start_text) - len(start_text.lstrip())
        )
        end_x = len(self.lines[last_line].text.rstrip())
        self.screen.selections.clear()
        self.screen.selections[self] = Selection(
            Offset(start_x, start), Offset(end_x, last_line)
        )

    def _selection_range_for_line(self, line_index: int) -> int | None:
        for index, (_classes, start, end) in enumerate(self.selection_ranges):
            if start <= line_index < end:
                return index
        return None

    def render_line(self, y: int) -> Strip:
        width = self.scrollable_content_region.width
        line_index = self.scroll_offset[1] + y
        if line_index >= len(self.lines):
            return Strip.blank(width, self.rich_style)

        line = self.lines[line_index]
        if line.cell_length >= width:
            scroll_x = self.scroll_offset[0]
            line = line.crop_extend(scroll_x, scroll_x + width, self.rich_style)
            return self._selectable_line(line, line_index, -scroll_x, scroll_x)

        line = self._selectable_line(line, line_index, 0, 0)
        left = (width - line.cell_length) // 2
        return Strip.join(
            [
                Strip.blank(left, self.rich_style),
                line,
                Strip.blank(width - line.cell_length - left, self.rich_style),
            ]
        )

    def _selectable_line(
        self, line: Strip, line_index: int, shift: int, offset_x: int
    ) -> Strip:
        if self.text_selection is not None:
            line = self._highlight_selection(
                line, self.text_selection, line_index, shift
            )
        return line.apply_style(self.rich_style).apply_offsets(offset_x, line_index)

    def _highlight_selection(
        self, line: Strip, selection: Selection, line_index: int, shift: int
    ) -> Strip:
        if (span := selection.get_span(line_index)) is None:
            return line
        start, end = span
        text = line.text
        if end == -1:
            end = len(text.rstrip())
        start = min(max(start, len(text) - len(text.lstrip())), len(text.rstrip()))
        start = max(0, start + shift)
        end = min(line.cell_length, max(start, end + shift))
        if end <= start:
            return line
        selected = line.crop(start, end)
        return Strip.join(
            [
                line.crop(0, start),
                Strip(
                    list(
                        Segment.apply_style(
                            selected._segments, post_style=self.selection_style
                        )
                    ),
                    selected.cell_length,
                ),
                line.crop(end),
            ]
        )

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        return selection.extract(
            "\n".join(line.text.rstrip() for line in self.lines)
        ), "\n"
