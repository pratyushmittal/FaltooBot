import asyncio
import threading
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, TypeVar

from faltoobot.chat.entries import Entry
from faltoobot.chat.runtime import ChatRuntime, build_chat_runtime
from faltoobot.chat.terminal import status_text
from faltoobot.config import Config

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    content: str
    paused: bool


@dataclass(frozen=True, slots=True)
class ChatSnapshot:
    entries: tuple[Entry, ...]
    queued: tuple[QueueSnapshot, ...]
    replying: bool
    session_title: str
    workspace: str
    status: str


class RuntimeBridge:
    def __init__(self, config: Config | None = None, name: str | None = None) -> None:
        self.config = config
        self.name = name
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(
            target=self.run_loop, name="faltoomac-runtime", daemon=True
        )
        self.ready = threading.Event()
        self.runtime: ChatRuntime | None = None

    def run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.ready.set()
        self.loop.run_forever()
        pending = asyncio.all_tasks(self.loop)
        for task in pending:
            task.cancel()
        if pending:
            self.loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True)
            )
        self.loop.close()

    def ensure_thread(self) -> None:
        if self.thread.is_alive():
            return
        self.thread.start()
        self.ready.wait()

    def wait(self, future: Future[T]) -> T:
        return future.result()

    def call(self, coro: Coroutine[Any, Any, T]) -> T:
        self.ensure_thread()
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return self.wait(future)

    async def start_runtime(self, notify: Callable[[], None]) -> None:
        self.runtime = build_chat_runtime(config=self.config, name=self.name)
        self.runtime.set_notifier(notify)
        await self.runtime.start()

    def start(self, notify: Callable[[], None]) -> None:
        self.call(self.start_runtime(notify))

    async def read_snapshot(self) -> ChatSnapshot:
        if self.runtime is None:
            raise RuntimeError("runtime is not ready")
        session = self.runtime.require_session()
        queued = tuple(
            QueueSnapshot(prompt.content, prompt.paused)
            for prompt in self.runtime.pending_prompts
        )
        return ChatSnapshot(
            entries=tuple(self.runtime.display_entries()),
            queued=queued,
            replying=self.runtime.current_reply_task is not None,
            session_title=f"{session.name} ({session.id})",
            workspace=str(session.workspace),
            status=status_text(self.runtime.config),
        )

    def snapshot(self) -> ChatSnapshot:
        return self.call(self.read_snapshot())

    async def submit_prompt(self, prompt: str) -> bool:
        if self.runtime is None:
            raise RuntimeError("runtime is not ready")
        return await self.runtime.submit(prompt)

    def submit(self, prompt: str) -> bool:
        return self.call(self.submit_prompt(prompt))

    def interrupt(self) -> bool:
        if self.runtime is None:
            return False
        future: Future[bool] = Future()

        def do_interrupt() -> None:
            if self.runtime is None:
                future.set_result(False)
                return
            future.set_result(self.runtime.interrupt())

        self.ensure_thread()
        self.loop.call_soon_threadsafe(do_interrupt)
        return future.result()

    async def close_runtime(self) -> None:
        if self.runtime is not None:
            await self.runtime.close()
            self.runtime = None

    def close(self) -> None:
        if self.thread.is_alive():
            self.call(self.close_runtime())
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=5)
