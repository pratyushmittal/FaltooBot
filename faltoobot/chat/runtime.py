import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from faltoobot.agent import ReplyResult, stream_reply
from faltoobot.config import Config, build_config
from faltoobot.store import (
    QueuedPrompt,
    Session,
    Turn,
    add_turn,
    cli_session,
    existing_cli_session,
    replace_queued_prompts,
    session_items,
    sync_assistant_turn,
)

from .entries import Entry, StreamState, history_entries, item_entries, item_key, tool_entry
from .images import display_prompt, prompt_message_item
from .prompts import (
    default_session_name,
    expand_saved_prompt,
    help_text,
    session_name,
    slash_commands,
)
from .terminal import open_in_default_editor


@dataclass(slots=True)
class ChatRuntime:
    config: Config
    name: str | None = None
    client: AsyncOpenAI | None = None
    session: Session | None = None
    own_client: bool = False
    pending_prompts: list[QueuedPrompt] = field(default_factory=list)
    processing_task: asyncio.Task[None] | None = None
    current_reply_task: asyncio.Task[ReplyResult] | None = None
    entries: list[Entry] = field(default_factory=list)
    live_entry: Entry | None = None
    notify: Callable[[], None] = field(default=lambda: None, repr=False)

    def require_session(self) -> Session:
        if self.session is None:
            raise RuntimeError("chat session is not ready")
        return self.session

    def require_client(self) -> AsyncOpenAI:
        if self.client is None:
            raise RuntimeError("chat session is not ready")
        return self.client

    def set_notifier(self, notify: Callable[[], None]) -> None:
        self.notify = notify

    def save_queue(self) -> None:
        if self.session is None:
            return
        self.session = replace_queued_prompts(self.require_session(), self.pending_prompts)

    def display_entries(self) -> list[Entry]:
        return [*self.entries, *([self.live_entry] if self.live_entry else [])]

    def append_entry(self, kind: str, content: str, *, notify: bool = True) -> None:
        self.entries.append(Entry(kind, content))
        if notify:
            self.notify()

    def cli_session(self, workspace: Path, name: str | None = None) -> Session:
        if name is None and (existing := existing_cli_session(self.config.sessions_dir, workspace)):
            return existing
        return cli_session(self.config.sessions_dir, session_name(name), workspace=workspace)

    def restore_queue(self) -> None:
        if self.session is None or not self.session.queued_prompts:
            return
        self.pending_prompts = [
            QueuedPrompt(prompt.content, True) for prompt in self.session.queued_prompts
        ]
        self.save_queue()

    def start_client(self) -> None:
        if self.client is None:
            self.client = AsyncOpenAI(api_key=self.config.openai_api_key)
            self.own_client = True

    async def start(self) -> None:
        if not self.config.openai_api_key:
            raise RuntimeError(f"openai.api_key is missing. Add it to {self.config.config_file}")
        self.session = self.cli_session(Path.cwd(), self.name)
        self.restore_queue()
        self.start_client()
        session = self.require_session()
        self.entries = [
            Entry("banner", " faltoochat "),
            Entry("meta", f"session: {session.name} ({session.id})"),
            Entry("meta", f"workspace: {session.workspace}"),
            Entry("meta", help_text()),
            *history_entries(session),
        ]

    async def close(self) -> None:
        if self.processing_task is not None:
            await self.processing_task
            self.processing_task = None
        if self.current_reply_task is not None:
            try:
                await self.current_reply_task
            except asyncio.CancelledError:
                pass
        if self.client and self.own_client:
            await self.client.close()

    def queued_prompt_text(self, prompt: str) -> str | None:
        text = prompt.strip()
        if not text:
            return None
        return expand_saved_prompt(self.config.root, text) or text

    async def submit(self, prompt: str) -> bool:
        text = prompt.strip()
        if not text:
            return True
        if (command_result := await self.handle_command(text)) is not None:
            return command_result
        queued = self.queued_prompt_text(text)
        if queued is None:
            return True
        if self.can_start_prompt_now():
            self.start_prompt_now(queued)
            return True
        self.enqueue_prompt(queued)
        self.notify()
        self.ensure_processing()
        return True

    def queue_prompt(self, prompt: str, *, paused: bool = False) -> None:
        if (queued := self.queued_prompt_text(prompt)) is None:
            return
        self.enqueue_prompt(queued, paused=paused)
        self.notify()

    def slash_commands(self) -> tuple[tuple[str, str], ...]:
        return slash_commands(self.config.root)

    def queued_prompts(self) -> tuple[str, ...]:
        return tuple(prompt.content for prompt in self.pending_prompts)

    def queued_prompt_items(self) -> tuple[QueuedPrompt, ...]:
        return tuple(self.pending_prompts)

    def enqueue_prompt(self, prompt: str, *, paused: bool = False) -> None:
        self.pending_prompts.append(QueuedPrompt(prompt, paused))
        self.save_queue()

    def pop_next_prompt(self) -> str | None:
        for index, prompt in enumerate(self.pending_prompts):
            if not prompt.paused:
                value = self.pending_prompts.pop(index).content
                self.save_queue()
                return value
        return None

    def remove_prompt(self, index: int) -> str | None:
        if 0 <= index < len(self.pending_prompts):
            prompt = self.pending_prompts.pop(index)
            self.save_queue()
            self.notify()
            return prompt.content
        return None

    def replace_prompt(self, index: int, prompt: str) -> bool:
        if 0 <= index < len(self.pending_prompts):
            self.pending_prompts[index].content = prompt
            self.save_queue()
            self.notify()
            return True
        return False

    def toggle_prompt_paused(self, index: int) -> bool | None:
        if 0 <= index < len(self.pending_prompts):
            prompt = self.pending_prompts[index]
            prompt.paused = not prompt.paused
            self.save_queue()
            self.notify()
            return prompt.paused
        return None

    def move_prompt(self, index: int, target: int) -> int | None:
        if not (0 <= index < len(self.pending_prompts) and 0 <= target < len(self.pending_prompts)):
            return None
        prompt = self.pending_prompts.pop(index)
        self.pending_prompts.insert(target, prompt)
        self.save_queue()
        self.notify()
        return target

    async def handle_command(self, text: str) -> bool | None:
        match text:
            case "/help":
                self.append_entry("meta", help_text())
                return True
            case "/tree":
                session = self.require_session()
                open_in_default_editor(session.messages_file)
                self.append_entry("opened", str(session.messages_file))
                return True
            case "/reset":
                session = self.require_session()
                self.session = self.cli_session(session.workspace, default_session_name())
                self.pending_prompts = []
                new_session = self.require_session()
                self.append_entry("meta", f"new session: {new_session.name} ({new_session.id})")
                return True
            case "/exit":
                return False
            case _:
                return None

    def can_start_prompt_now(self) -> bool:
        return (
            self.current_reply_task is None
            and not self.pending_prompts
            and (self.processing_task is None or self.processing_task.done())
        )

    def start_prompt_now(self, prompt: str) -> None:
        display_text = display_prompt(prompt, self.require_session().workspace)
        self.append_entry("you", display_text, notify=False)
        self.notify()
        self.processing_task = asyncio.create_task(self.process_now(prompt, display_text))

    def ensure_processing(self) -> None:
        if self.processing_task is None or self.processing_task.done():
            self.processing_task = asyncio.create_task(self.process_pending())

    async def process_pending(self) -> None:
        while prompt := self.pop_next_prompt():
            self.notify()
            await self.handle_prompt(prompt)
        self.notify()

    async def wait_until_idle(self) -> None:
        if self.processing_task is not None:
            await self.processing_task
            self.processing_task = None

    async def process_now(self, prompt: str, display_text: str) -> None:
        await self.handle_prompt(prompt, display_text=display_text, already_rendered=True)
        if self.pending_prompts:
            await self.process_pending()
        else:
            self.notify()

    def interrupt(self) -> bool:
        if self.current_reply_task is None or self.current_reply_task.done():
            return False
        self.current_reply_task.cancel()
        return True

    def close_stream(self, state: StreamState) -> None:
        if state.active_kind is None or self.live_entry is None:
            return
        self.entries.append(self.live_entry)
        self.live_entry = None
        state.active_kind = None
        self.notify()

    def replace_last_entry(self, kind: str, content: str) -> None:
        for index in range(len(self.entries) - 1, -1, -1):
            if self.entries[index].kind == kind:
                self.entries[index] = Entry(kind, content)
                return
        self.entries.append(Entry(kind, content))

    def replace_last_bot_entry(self, content: str) -> None:
        self.replace_last_entry("bot", content)

    def stream_delta(self, state: StreamState, kind: str, delta: str) -> None:
        if not delta:
            return
        if state.active_kind != kind:
            self.close_stream(state)
            state.active_kind = kind
            self.live_entry = Entry(kind, "")
        if kind == "bot":
            state.saw_bot = True
        if kind == "thinking":
            state.saw_thinking = True
        if self.live_entry is not None and delta:
            self.live_entry = Entry(kind, self.live_entry.content + delta)
            self.notify()

    def sync_assistant_progress(
        self,
        content: str,
        items: list[dict[str, Any]],
        *,
        usage: dict[str, Any] | None = None,
        instructions: str | None = None,
    ) -> Turn | None:
        if not items and not content:
            return None
        self.session = sync_assistant_turn(
            self.require_session(),
            content,
            items=items,
            usage=usage,
            instructions=instructions,
        )
        return self.require_session().messages[-1]

    def store_assistant_turn(self, result: ReplyResult) -> Turn:
        turn = self.sync_assistant_progress(
            result["text"],
            list(result["output_items"]),
            usage=result["usage"],
            instructions=result["instructions"],
        )
        if turn is None:
            raise RuntimeError("assistant reply was empty")
        return turn

    def render_assistant_turn(self, turn: Turn, state: StreamState) -> None:
        self.entries.extend(
            entry
            for item in turn.items
            if item_key(item) not in state.tool_keys
            for entry in item_entries(item)
            if not (state.saw_thinking and entry.kind == "thinking")
        )
        if turn.content:
            if state.saw_bot:
                self.replace_last_bot_entry(turn.content)
            else:
                self.entries.append(Entry("bot", turn.content))
        self.notify()

    async def handle_prompt(
        self,
        prompt: str,
        *,
        display_text: str | None = None,
        already_rendered: bool = False,
    ) -> None:
        session = self.require_session()
        optimistic_text = display_text or display_prompt(prompt, session.workspace)
        if not already_rendered:
            self.append_entry("you", optimistic_text)
        display_text, message_item = await prompt_message_item(
            self.require_client(),
            session.workspace,
            prompt,
        )
        session = add_turn(
            session,
            "user",
            display_text,
            items=[message_item] if message_item else None,
        )
        self.session = session
        if already_rendered and display_text != optimistic_text:
            self.replace_last_entry("you", display_text)
            self.notify()
        state = StreamState()

        async def on_text_delta(delta: str) -> None:
            self.stream_delta(state, "bot", delta)

        async def on_reasoning_delta(delta: str) -> None:
            self.stream_delta(state, "thinking", delta)

        async def on_reasoning_done() -> None:
            if state.active_kind == "thinking":
                self.close_stream(state)

        async def on_output_item(item: dict[str, Any]) -> None:
            if text := tool_entry(item):
                self.close_stream(state)
                self.append_entry("tool", text)
                if key := item_key(item):
                    state.tool_keys.add(key)

        async def on_stream_end(items: list[dict[str, Any]], text: str) -> None:
            self.sync_assistant_progress(text, items)

        task = asyncio.create_task(
            stream_reply(
                self.require_client(),
                self.config,
                session,
                session_items(session),
                on_text_delta=on_text_delta,
                on_reasoning_delta=on_reasoning_delta,
                on_reasoning_done=on_reasoning_done,
                on_output_item=on_output_item,
                on_stream_end=on_stream_end,
            )
        )
        self.current_reply_task = task
        try:
            result = await task
        except asyncio.CancelledError:
            self.close_stream(state)
            self.append_entry("meta", "reply interrupted")
            return
        except Exception as exc:
            self.close_stream(state)
            self.append_entry("error", str(exc))
            return
        finally:
            self.current_reply_task = None

        self.close_stream(state)
        self.render_assistant_turn(self.store_assistant_turn(result), state)


def build_chat_runtime(
    config: Config | None = None,
    name: str | None = None,
    client: AsyncOpenAI | None = None,
) -> ChatRuntime:
    return ChatRuntime(config=config or build_config(), name=name, client=client)
