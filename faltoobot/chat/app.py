import asyncio
import time
from typing import Any

from openai import AsyncOpenAI
from textual import events, on
from textual.app import App, ComposeResult, ScreenStackError
from textual.binding import Binding
from textual.containers import Center, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Static, TextArea

from faltoobot.config import Config, build_config

from .prompts import completed_slash_query, slash_query, slash_suggestions
from .runtime import build_chat_runtime
from .state import QueueState, SlashState, TranscriptState
from .terminal import input_hint
from .widgets import (
    Composer,
    EntryBlock,
    LiveMarkdownBlock,
    QueueItem,
    SlashCommandItem,
)

SCROLL_SETTLE_DURATION = 1.0
SCROLL_SETTLE_INTERVAL = 0.02


class FaltooChatApp(App[None]):
    CSS = """
    App {
        color: $text;
        background: $background;
        link-background: transparent;
        link-background-hover: transparent;
        link-color: $primary;
        link-color-hover: $accent;
        link-style: bold underline;
        link-style-hover: bold underline;
    }

    Screen {
        layout: vertical;
        color: $text;
        background: $background;
    }

    #shell {
        height: 1fr;
        background: $background;
    }

    #transcript {
        width: 1fr;
        height: 1fr;
        layout: vertical;
        align-horizontal: center;
        overflow-y: auto;
        overflow-x: hidden;
        background: $background;
        padding: 1 2;
        border: none;
    }

    #queue {
        height: auto;
        max-height: 8;
        layout: vertical;
        background: $background;
        border: round $primary;
        border-bottom: none;
        border-title-align: left;
        border-title-color: $primary;
        border-title-background: $background;
        padding: 0;
    }

    #commands {
        height: auto;
        layout: vertical;
        background: $background;
        border: round $primary;
        border-bottom: none;
        border-title-align: left;
        border-title-color: $primary;
        border-title-background: $background;
        padding: 0 0 0 0;
    }

    .slash-command-item {
        height: 1;
        layout: horizontal;
        align: left middle;
        padding: 0 1;
        background: $background;
        color: $text;
    }

    .slash-command-name {
        width: 20;
        text-style: bold;
        color: $accent;
    }

    .slash-command-detail {
        width: 1fr;
        color: $text-muted;
    }

    .queue-item {
        height: 1;
        align: left middle;
        padding: 0 1;
        background: $background;
        color: $text;
        margin: 0;
    }

    .queue-item.-selected {
        background: $primary 18%;
    }

    .queue-item.-selected .queue-text {
        text-style: bold;
    }

    .queue-text {
        width: 1fr;
        height: 1;
        color: $text;
    }

    #footer {
        width: 1fr;
        max-width: 80;
        height: auto;
        layout: vertical;
    }

    #composer {
        width: 1fr;
        height: 6;
        min-height: 3;
        background: $surface;
        color: $text;
        padding: 0 1;
        border: none;
    }

    #status {
        width: 1fr;
        height: 1;
        padding: 0 2;
        background: $surface;
        color: $text-disabled;
        text-style: none;
    }

    Markdown {
        background: transparent;
        link-background: transparent;
        link-background-hover: transparent;
        link-color: $primary;
        link-color-hover: $accent;
        link-style: bold underline;
        link-style-hover: bold underline;
    }

    MarkdownBlock {
        link-background: transparent;
        link-background-hover: transparent;
        link-color: $primary;
        link-color-hover: $accent;
        link-style: bold underline;
        link-style-hover: bold underline;
    }

    Markdown MarkdownFence {
        background: transparent;
        color: $text;
    }

    Markdown MarkdownFence > Label {
        background: transparent;
        color: $text;
    }

    Markdown MarkdownBlock > .code_inline {
        background: $surface;
        color: $warning;
    }

    #transcript {
        scrollbar-background: $background;
        scrollbar-background-hover: $background;
        scrollbar-background-active: $background;
        scrollbar-color: $text-muted;
        scrollbar-color-hover: $primary;
        scrollbar-color-active: $accent;
        scrollbar-corner-color: $background;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "interrupt_or_quit", "Interrupt", show=False),
    ]

    def __init__(
        self,
        config: Config | None = None,
        name: str | None = None,
        client: AsyncOpenAI | None = None,
        terminal_dark: bool | None = None,
    ) -> None:
        super().__init__()
        chat_config = config or build_config()
        self.theme_file = chat_config.root / "chat-theme.txt"
        if self.theme_file.exists():
            theme = self.theme_file.read_text(encoding="utf-8").strip()
            if theme in self.available_themes:
                self.theme = theme
        elif terminal_dark is not None:
            self.theme = "textual-dark" if terminal_dark else "textual-light"
        self.runtime = build_chat_runtime(config=chat_config, name=name, client=client)
        self.transcript_state = TranscriptState()
        self.queue_state = QueueState()
        self.slash_state = SlashState()

    def watch_theme(self, theme_name: str) -> None:
        if theme_name not in self.available_themes:
            return
        self.theme_file.parent.mkdir(parents=True, exist_ok=True)
        self.theme_file.write_text(f"{theme_name}\n", encoding="utf-8")

    @property
    def follow_transcript(self) -> bool:
        return self.transcript_state.follow

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            yield VerticalScroll(id="transcript")
            with Center():
                with Vertical(id="footer"):
                    queue = Vertical(id="queue")
                    queue.border_title = "Queue"
                    yield queue
                    commands = Vertical(id="commands")
                    commands.border_title = "Commands"
                    yield commands
                    yield Composer(
                        id="composer",
                        text="",
                        soft_wrap=True,
                        show_line_numbers=False,
                        highlight_cursor_line=False,
                        placeholder="Type a message or /help",
                    )
                    yield Static("", id="status")

    def transcript(self) -> VerticalScroll:
        return self.query_one("#transcript", VerticalScroll)

    def composer(self) -> Composer:
        return self.query_one("#composer", Composer)

    def queue(self) -> Vertical:
        return self.query_one("#queue", Vertical)

    def commands(self) -> Vertical:
        return self.query_one("#commands", Vertical)

    def status(self) -> Static:
        return self.query_one("#status", Static)

    def focus_composer(self) -> None:
        try:
            self.composer().focus()
        except (NoMatches, ScreenStackError):
            return

    def cancel_transcript_settle_task(self) -> None:
        if self.transcript_state.settle_task is None:
            return
        self.transcript_state.settle_task.cancel()
        self.transcript_state.settle_task = None

    async def settle_transcript_end(self) -> None:
        deadline = time.monotonic() + SCROLL_SETTLE_DURATION
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(SCROLL_SETTLE_INTERVAL)
                if not self.transcript_state.follow:
                    return
                self.scroll_transcript_end_once()
                self.call_after_refresh(self.scroll_transcript_end_once)
        except asyncio.CancelledError:
            return
        finally:
            if asyncio.current_task() is self.transcript_state.settle_task:
                self.transcript_state.settle_task = None

    def scroll_transcript_end_once(self) -> None:
        try:
            self.transcript().scroll_end(animate=False, immediate=True)
        except NoMatches:
            return

    def scroll_transcript_end(self, *, settle: bool = False) -> None:
        self.cancel_transcript_settle_task()
        self.scroll_transcript_end_once()
        self.call_after_refresh(self.scroll_transcript_end_once)
        if settle:
            self.transcript_state.settle_task = asyncio.create_task(
                self.settle_transcript_end()
            )

    def restore_transcript_scroll(self, y: float) -> None:
        try:
            self.transcript().scroll_to(y=y, animate=False, immediate=True)
        except NoMatches:
            return

    def stop_following_transcript(self) -> None:
        self.transcript_state.follow = False
        self.cancel_transcript_settle_task()

    def update_transcript_follow_from_position(self) -> None:
        try:
            self.transcript_state.follow = self.transcript().is_vertical_scroll_end
        except NoMatches:
            return

    def track_manual_transcript_scroll(self) -> None:
        self.stop_following_transcript()
        self.set_timer(0, self.update_transcript_follow_from_position)

    async def on_mount(self) -> None:
        self.runtime.set_notifier(self.sync_view)
        await self.runtime.start()
        self.sync_view(force=True)
        self.scroll_transcript_end()
        self.call_after_refresh(self.focus_composer)

    async def on_unmount(self) -> None:
        self.cancel_transcript_settle_task()
        await self.runtime.close()

    async def on_composer_submitted(self, message: Composer.Submitted) -> None:
        composer = self.composer()
        composer.load_text("")
        self.transcript_state.follow = True
        if message.paused:
            self.runtime.queue_prompt(message.value, paused=True)
            self.sync_view()
            return
        if not await self.runtime.submit(message.value):
            self.runtime.pending_prompts.clear()
            self.runtime.interrupt()
            self.exit()
            return
        self.sync_view()

    def refresh_ui(self) -> None:
        try:
            self.sync_view()
        except NoMatches:
            return

    def sync_view(self, force: bool = False) -> None:
        queue_layout_changed = self.refresh_queue(force=force)
        self.refresh_commands(force=force)
        self.refresh_status()
        self.refresh_transcript(force=force or queue_layout_changed)

    def refresh_commands(self, *, force: bool = False) -> None:
        query = slash_query(self.composer().text)
        if (
            self.slash_state.dismissed_query is not None
            and query != self.slash_state.dismissed_query
        ):
            self.slash_state.dismissed_query = None
        commands = self.runtime.slash_commands()
        suggestions = (
            ()
            if query is not None and query == self.slash_state.dismissed_query
            else slash_suggestions(self.composer().text, commands)
        )
        if not force and suggestions == self.slash_state.snapshot:
            return
        commands = self.commands()
        commands.remove_children()
        items = [SlashCommandItem(command, detail) for command, detail in suggestions]
        if items:
            commands.mount(*items)
            commands.display = True
        else:
            commands.display = False
        self.slash_state.snapshot = suggestions

    def refresh_status(self) -> None:
        self.status().update(
            input_hint(
                self.runtime.config,
                replying=self.runtime.current_reply_task is not None,
                queued=len(self.runtime.pending_prompts),
            )
        )

    def normalize_queue_selection(self) -> None:
        queued = self.runtime.queued_prompts()
        if not queued:
            self.queue_state.selected = None
        elif self.queue_state.selected is not None and self.queue_state.selected >= len(
            queued
        ):
            self.queue_state.selected = len(queued) - 1

    def refresh_queue(self, *, force: bool = False) -> bool:
        queued = self.runtime.queued_prompt_items()
        queue_snapshot = tuple((prompt.content, prompt.paused) for prompt in queued)
        self.normalize_queue_selection()
        selection_changed = (
            self.queue_state.selected != self.queue_state.selected_snapshot
        )
        layout_changed = queue_snapshot != self.queue_state.snapshot
        if not force and not layout_changed and not selection_changed:
            return False
        queue = self.queue()
        if not force and selection_changed and not layout_changed:
            for child in queue.children:
                if isinstance(child, QueueItem):
                    child.select(child.index == self.queue_state.selected)
            self.queue_state.selected_snapshot = self.queue_state.selected
            return False
        queue.remove_children()
        items = [QueueItem(index, prompt) for index, prompt in enumerate(queued)]
        for item in items:
            item.select(item.index == self.queue_state.selected)
        if items:
            queue.mount(*items)
            queue.display = True
        else:
            queue.display = False
        self.queue_state.snapshot = queue_snapshot
        self.queue_state.selected_snapshot = self.queue_state.selected
        return layout_changed or force

    def transcript_snapshot(self) -> tuple[tuple[str, str, bool], ...]:
        entries = list(self.runtime.entries)
        live = self.runtime.live_entry
        return tuple((entry.kind, entry.content, False) for entry in entries) + (
            ((live.kind, live.content, True),) if live else ()
        )

    def sync_transcript_entries(
        self,
        transcript: VerticalScroll,
        entries: list[Any],
        *,
        force: bool = False,
    ) -> None:
        previous_rendered = tuple(
            (block.entry.kind, block.entry.content)
            for block in self.transcript_state.blocks
        )
        rendered = tuple((entry.kind, entry.content) for entry in entries)
        append_only = rendered[: len(self.transcript_state.blocks)] == previous_rendered
        if force or not append_only:
            transcript.remove_children()
            self.transcript_state.blocks = []
            self.transcript_state.stream_block = None
            blocks = [EntryBlock(entry) for entry in entries]
            self.transcript_state.blocks = blocks
            if blocks:
                transcript.mount(*blocks)
            return
        new_entries = entries[len(self.transcript_state.blocks) :]
        if not new_entries:
            return
        blocks = [EntryBlock(entry) for entry in new_entries]
        self.transcript_state.blocks.extend(blocks)
        transcript.mount(*blocks)

    def remove_stream_block(self) -> None:
        if self.transcript_state.stream_block is None:
            return
        self.transcript_state.stream_block.remove()
        self.transcript_state.stream_block = None

    def sync_transcript_live_entry(
        self,
        transcript: VerticalScroll,
        entries: list[Any],
        live: Any,
    ) -> None:
        stream_block = self.transcript_state.stream_block
        if live is None:
            if stream_block is None:
                return
            final_index = len(self.transcript_state.blocks)
            if final_index < len(entries):
                final_entry = entries[final_index]
                if stream_block.entry.kind == final_entry.kind:
                    committed = EntryBlock(final_entry)
                    stream_block.remove()
                    transcript.mount(committed)
                    self.transcript_state.blocks.append(committed)
                    self.transcript_state.stream_block = None
                    return
            self.remove_stream_block()
            return
        if stream_block is None:
            self.transcript_state.stream_block = LiveMarkdownBlock(live)
            transcript.mount(self.transcript_state.stream_block)
            return
        if not stream_block.set_entry(live):
            stream_block.remove()
            self.transcript_state.stream_block = LiveMarkdownBlock(live)
            transcript.mount(self.transcript_state.stream_block)

    def refresh_transcript(self, *, force: bool = False) -> None:
        entries = list(self.runtime.entries)
        live = self.runtime.live_entry
        snapshot = self.transcript_snapshot()
        if not force and snapshot == self.transcript_state.snapshot:
            return

        transcript = self.transcript()
        previous_scroll = transcript.scroll_y
        had_live = self.transcript_state.stream_block is not None or live is not None
        self.sync_transcript_entries(transcript, entries, force=force)
        self.sync_transcript_live_entry(transcript, entries, live)

        if self.transcript_state.follow:
            self.scroll_transcript_end(settle=had_live)
        else:
            self.restore_transcript_scroll(previous_scroll)
        self.transcript_state.snapshot = snapshot

    @on(TextArea.Changed, "#composer")
    def on_composer_changed(self, _: TextArea.Changed) -> None:
        self.refresh_commands()

    def dismiss_slash_commands(self) -> bool:
        query = slash_query(self.composer().text)
        if query is None or not self.slash_state.snapshot:
            return False
        self.slash_state.dismissed_query = query
        self.refresh_commands(force=True)
        return True

    @on(SlashCommandItem.Picked)
    def on_slash_command_item_picked(self, message: SlashCommandItem.Picked) -> None:
        self.slash_state.dismissed_query = None
        composer = self.composer()
        composer.load_text(message.command)
        self.focus_composer()
        self.refresh_commands(force=True)

    @on(events.MouseScrollUp, "#transcript")
    def on_transcript_mouse_scroll_up(self, event: Any) -> None:
        event.stop()
        self.track_manual_transcript_scroll()

    @on(events.MouseScrollDown, "#transcript")
    def on_transcript_mouse_scroll_down(self, event: Any) -> None:
        event.stop()
        self.track_manual_transcript_scroll()

    def action_interrupt_or_quit(self) -> None:
        if not self.runtime.interrupt():
            self.exit()

    def queue_selection_after_change(self, index: int) -> int | None:
        total = len(self.runtime.pending_prompts)
        return min(index, total - 1) if total else None

    def edit_queue(self, index: int) -> None:
        if (prompt := self.runtime.remove_prompt(index)) is None:
            return
        self.queue_state.selected = self.queue_selection_after_change(index)
        composer = self.composer()
        composer.load_text(prompt)
        self.focus_composer()
        self.sync_view()

    def delete_queue(self, index: int) -> None:
        if self.runtime.remove_prompt(index) is None:
            return
        self.queue_state.selected = self.queue_selection_after_change(index)
        self.sync_view()

    def move_queue(self, index: int, target: int) -> None:
        if (new_index := self.runtime.move_prompt(index, target)) is None:
            return
        self.queue_state.selected = new_index
        self.sync_view()

    def move_queue_selection(self, delta: int) -> bool:
        total = len(self.runtime.pending_prompts)
        if not total or self.queue_state.selected is None:
            return False
        if delta < 0:
            self.queue_state.selected = max(0, self.queue_state.selected - 1)
        else:
            self.queue_state.selected = min(total - 1, self.queue_state.selected + 1)
        self.focus_composer()
        self.sync_view()
        return True

    def toggle_queue_focus(self) -> bool:
        total = len(self.runtime.pending_prompts)
        if not total:
            return True
        self.queue_state.selected = (
            None if self.queue_state.selected is not None else total - 1
        )
        self.focus_composer()
        self.sync_view()
        return True

    def complete_slash_command(self) -> bool:
        completed = completed_slash_query(
            self.composer().text, self.slash_state.snapshot
        )
        if completed is None:
            return False
        self.slash_state.dismissed_query = None
        self.composer().load_text(completed)
        self.focus_composer()
        self.refresh_commands(force=True)
        return True

    def handle_selected_queue_key(self, key: str) -> bool:
        actions = {
            "up": lambda: self.move_queue_selection(-1),
            "down": lambda: self.move_queue_selection(1),
            "enter": self.action_edit_selected_queue,
            "delete": self.action_delete_selected_queue,
            "backspace": self.action_delete_selected_queue,
            "space": self.action_toggle_selected_queue_pause,
            "shift+up": self.action_move_selected_queue_up,
            "shift+down": self.action_move_selected_queue_down,
        }
        action = actions.get(key)
        if action is None:
            return False
        result = action()
        return True if result is None else result

    def handle_composer_key(self, key: str) -> bool:
        if key == "escape":
            return self.dismiss_slash_commands()
        if key == "tab":
            return self.complete_slash_command() or self.toggle_queue_focus()
        if self.queue_state.selected is None:
            return False
        return self.handle_selected_queue_key(key)

    @on(QueueItem.Picked)
    def on_queue_item_picked(self, message: QueueItem.Picked) -> None:
        self.queue_state.selected = message.index
        self.focus_composer()
        self.sync_view()

    def action_edit_selected_queue(self) -> None:
        if self.queue_state.selected is not None:
            self.edit_queue(self.queue_state.selected)

    def toggle_queue_pause(self, index: int) -> None:
        if (paused := self.runtime.toggle_prompt_paused(index)) is not None:
            self.queue_state.selected = index
            if not paused:
                self.runtime.ensure_processing()
            self.sync_view()

    def action_delete_selected_queue(self) -> None:
        if self.queue_state.selected is not None:
            self.delete_queue(self.queue_state.selected)

    def action_toggle_selected_queue_pause(self) -> None:
        if self.queue_state.selected is not None:
            self.toggle_queue_pause(self.queue_state.selected)

    def action_move_selected_queue_up(self) -> None:
        if self.queue_state.selected not in {None, 0}:
            self.move_queue(self.queue_state.selected, self.queue_state.selected - 1)

    def action_move_selected_queue_down(self) -> None:
        if self.queue_state.selected is None:
            return
        if self.queue_state.selected < len(self.runtime.pending_prompts) - 1:
            self.move_queue(self.queue_state.selected, self.queue_state.selected + 1)
