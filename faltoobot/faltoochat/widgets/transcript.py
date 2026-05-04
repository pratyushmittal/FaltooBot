from typing import Any, cast

from markdown_it import MarkdownIt
from pygments.token import Token
from rich.console import Console, ConsoleOptions, RenderResult
from rich import box
from rich.markdown import BlockQuote, CodeBlock, Markdown as RichMarkdown
from rich.markdown import TableElement
from rich.segment import Segment
from rich.style import Style
from rich.syntax import Syntax, SyntaxTheme
from rich.text import Text
from rich.table import Table
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
    Token.Name.Builtin.Pseudo: "variable.builtin",
    Token.Name.Class: "class",
    Token.Name.Function: "function",
    Token.Name.Tag: "tag",
    Token.Name.Variable: "variable",
    Token.Operator: "operator",
}


class _TextAreaSyntaxTheme(SyntaxTheme):
    def __init__(self, app_theme: str, background_style: Style) -> None:
        name = "github_light" if "light" in app_theme else "vscode_dark"
        theme = cast(TextAreaTheme, TextAreaTheme.get_builtin_theme(name))
        base_style = theme.base_style or Style()
        self.base_style = Style(color=base_style.color)
        self.syntax_styles = theme.syntax_styles
        self.background_style = background_style

    def get_style_for_token(self, token_type: Any) -> Style:
        while token_type is not None:
            if scope := _TOKEN_SCOPES.get(token_type):
                if style := self.syntax_styles.get(scope):
                    return style
                return (
                    self.syntax_styles.get("variable", self.base_style)
                    + Style(italic=True)
                    if scope == "variable.builtin"
                    else self.base_style
                )
            token_type = token_type.parent
        return self.base_style

    def get_background_style(self) -> Style:
        return self.background_style


class _BlockQuote(BlockQuote):
    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        style = console.get_style("markdown.block_quote_bar")
        for line in console.render_lines(
            self.elements, options.update(width=options.max_width - 4), style=self.style
        ):
            yield Segment("▌ ", style)
            yield from line
            yield Segment.line()


class _CodeBlock(CodeBlock):
    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        yield Syntax(
            str(self.text).rstrip(),
            self.lexer_name,
            theme=self.theme,
            word_wrap=True,
            padding=(1, 3),
        )


class _Table(TableElement):
    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        table = Table(
            box=box.SQUARE,
            padding=(0, 1),
            style="markdown.table.border",
            header_style="markdown.table.header",
            border_style="markdown.table.border",
            show_edge=True,
        )
        if self.header is not None and self.header.row is not None:
            for column in self.header.row.cells:
                table.add_column(column.content.copy())
        if self.body is not None:
            for row in self.body.rows:
                table.add_row(*(cell.content for cell in row.cells))
        yield table


class _Markdown(RichMarkdown):
    elements = {
        **RichMarkdown.elements,
        "blockquote_open": _BlockQuote,
        "fence": _CodeBlock,
        "code_block": _CodeBlock,
        "table_open": _Table,
    }


def _message_blocks(text: str, classes: str) -> list[tuple[str, str]]:
    if classes != "tool" or SHELL_COMMAND_SEPARATOR not in text:
        return [(text, classes)]
    summary, command = text.split(SHELL_COMMAND_SEPARATOR, maxsplit=1)
    blocks = [(summary, "tool tool-summary")]
    if command := command.strip():
        blocks.append((command, "tool tool-command"))
    return blocks


def _selection_parts(text: str, classes: str) -> list[str]:
    if classes != "answer":
        return [text]
    lines = text.splitlines()
    return [
        "\n".join(lines[start:end])
        for token in MarkdownIt().parse(text)
        if token.level == 0
        and token.map is not None
        and not token.type.endswith("_close")
        for start, end in [token.map]
    ] or [text]


class _TranscriptBlock:
    def __init__(self, text: str, classes: str, owner: RichLog) -> None:
        self.text = text
        self.classes = set(classes.split())
        self.owner = owner

    def __rich_console__(  # noqa: C901
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        colors = self.owner.app.current_theme.to_color_system().generate()
        background = Color.parse(colors["background"])
        foreground = Color.parse(colors["foreground"])
        is_light = "light" in self.owner.app.theme

        def color(name: str):
            return Color.parse(colors[name]).rich_color

        def tint(name: str, amount: float):
            return background.blend(Color.parse(colors[name]), amount).rich_color

        text = Style(color=self.owner.styles.color.rich_color)
        muted = Style(color=foreground.blend(background, 0.4).rich_color)
        quote_bar = Style(color=color("text-secondary" if is_light else "text-primary"))
        inline_code = (
            Style(color=color("text-error"), bgcolor=tint("error", 0.05))
            if is_light
            else Style(color=color("text-warning"), bgcolor=tint("warning", 0.10))
        )
        code_block = text + Style(
            bgcolor=(
                background.blend(Color(255, 255, 255), 0.30).rich_color
                if is_light
                else tint("foreground", 0.04)
            )
        )
        block_style, border_style, has_border, padding, margin = (
            text,
            Style.null(),
            False,
            1,
            1,
        )
        if self.classes & {"history-summary", "thinking"}:
            block_style = muted
        elif "user" in self.classes:
            block_style, border_style, has_border = (
                text + Style(bgcolor=tint("primary", 0.15)),
                quote_bar + Style(bgcolor=tint("primary", 0.15)),
                True,
            )
        elif "tool-summary" in self.classes:
            block_style, margin = text + Style(bgcolor=tint("warning", 0.32)), 0
        elif "tool" in self.classes:
            block_style, padding = muted + Style(bgcolor=tint("warning", 0.08)), 2
        elif "unknown" in self.classes:
            block_style, border_style, has_border = (
                text + Style(bgcolor=tint("error", 0.12)),
                Style(color=color("error"), bgcolor=tint("error", 0.12)),
                True,
            )

        renderable = Text(self.text)
        if self.classes & {"history-summary", "unknown"}:
            renderable = Text.from_markup(self.text)
        elif "tool-command" not in self.classes:
            renderable = _Markdown(
                self.text,
                code_theme=_TextAreaSyntaxTheme(self.owner.app.theme, code_block),  # type: ignore
            )

        content_width = max(
            1, min(MAX_BLOCK_WIDTH, options.max_width) - int(has_border) - padding * 2
        )
        with console.use_theme(
            RichTheme(
                {
                    "markdown.code": inline_code,
                    "markdown.code_inline": inline_code,
                    "markdown.code_block": code_block,
                    "markdown.block_quote": text,
                    "markdown.block_quote_bar": quote_bar,
                    **{
                        f"markdown.h{level}": Style(color=color("primary"))
                        for level in range(1, 7)
                    },
                    "markdown.link": Style(color=color("text-accent"), underline=True),
                    "markdown.table.border": Style(color=color("foreground")),
                    "markdown.table.header": Style(color=color("primary"), bold=True),
                }
            )
        ):
            lines = list(
                Segment.split_lines(
                    console.render(renderable, options.update_width(content_width))
                )
            ) or [[]]
        if "tool-command" in self.classes:
            lines = lines[:3]
        if "review-comments" in self.classes:
            lines = [[]] + lines + [[]]

        for line in lines:
            line = Segment.adjust_line_length(
                list(Segment.apply_style(line, style=block_style)),
                content_width,
                block_style,
            )
            if has_border:
                yield Segment("▌", border_style)
            yield Segment(" " * padding, block_style)
            yield from line
            yield Segment(" " * padding, block_style)
            yield Segment.line()
        for _ in range(margin):
            yield Segment.line()


class TranscriptLog(RichLog):
    ALLOW_SELECT = True
    auto_links = False
    can_focus = False
    FOCUS_ON_CLICK = False

    def __init__(self, **kwargs: Any) -> None:
        self.messages: list[tuple[str, str]] = []
        self.message_ranges: list[tuple[str, int, int]] = []
        self.selection_ranges: list[tuple[str, int, int]] = []
        self._last_click_line: int | None = None
        self._render_width = 0
        super().__init__(wrap=True, markup=False, **kwargs)

    def on_resize(self, event: Resize) -> None:
        old_render_width = self._render_width
        super().on_resize(event)
        render_width = min(MAX_BLOCK_WIDTH, self.scrollable_content_region.width)
        if event.size.width and self.messages and render_width != old_render_width:
            self._render_messages()

    def refresh_theme(self) -> None:
        if self._size_known:
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
        if not self._size_known:
            return
        self._clear_selection()
        start = len(self.lines)
        parts = _selection_parts(text, classes)
        for index, part in enumerate(parts):
            part_start = len(self.lines)
            self._render_width = min(
                MAX_BLOCK_WIDTH, self.scrollable_content_region.width
            )
            self.write(
                _TranscriptBlock(part, classes, self),
                width=self._render_width,
                scroll_end=scroll_end and index == len(parts) - 1,
            )
            self.selection_ranges.append((classes, part_start, len(self.lines)))
        self.message_ranges.append((classes, start, len(self.lines)))
        self.refresh()

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

    def _clear_selection(self) -> None:
        if self in self.screen.selections:
            self.screen.selections = {
                widget: selection
                for widget, selection in self.screen.selections.items()
                if widget is not self
            }

    def _render_messages(self) -> None:
        messages = self.messages
        self.messages = []
        self.clear_messages()
        for text, classes in messages:
            self.write_message(text, classes, scroll_end=False)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if offset := event.get_content_offset(self):
            self._last_click_line = self.scroll_offset[1] + offset.y

    def text_select_all(self) -> None:
        if self._last_click_line is not None:
            self._select_block_at_line(self._last_click_line)

    async def _on_click(self, event: events.Click) -> None:
        if (
            event.widget is self
            and self.allow_select
            and self.screen.allow_select
            and self.app.ALLOW_SELECT
        ):
            double_click = 2
            if event.chain == double_click and not event.delta_x and not event.delta_y:
                self.text_select_all()
                event.stop()
                return

    def _select_block_at_line(self, line_index: int) -> None:
        for _classes, start, end in self.selection_ranges:
            if not start <= line_index < end:
                continue
            last_line = max(start, end - 1)
            while last_line > start and not self.lines[last_line].text.rstrip():
                last_line -= 1
            start_text = self.lines[start].text
            start_x = (
                2
                if start_text.startswith("▌ ")
                else len(start_text) - len(start_text.lstrip())
            )
            end_x = len(self.lines[last_line].text.rstrip())
            self.screen.selections = {  # type: ignore
                self: Selection(Offset(start_x, start), Offset(end_x, last_line))
            }
            return

    def render_line(self, y: int) -> Strip:
        width = self.scrollable_content_region.width
        line_index = self.scroll_offset[1] + y
        if line_index >= len(self.lines):
            return Strip.blank(width, self.rich_style)

        line = self.lines[line_index]
        shift, scroll_x = 0, 0
        if line.cell_length >= width:
            scroll_x = self.scroll_offset[0]
            line = line.crop_extend(scroll_x, scroll_x + width, self.rich_style)
            shift = -scroll_x
        if self.text_selection is not None:
            line = self._highlight_selection(
                line, self.text_selection, line_index, shift
            )
        line = line.apply_style(self.rich_style).apply_offsets(scroll_x, line_index)
        if scroll_x:
            return line
        left = (width - line.cell_length) // 2
        return Strip.join(
            [
                Strip.blank(left, self.rich_style),
                line,
                Strip.blank(width - line.cell_length - left, self.rich_style),
            ]
        )

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
