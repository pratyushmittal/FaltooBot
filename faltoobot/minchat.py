import sys
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from openai.types.responses import (
    ResponseFunctionToolCallOutputItem,
    ResponseInputItemParam,
)
from textual import getters
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical, VerticalScroll
from textual.widgets import Markdown, TextArea

from faltoobot import sessions
from faltoobot.chat.terminal import open_in_default_editor
from faltoobot.placeholders import get_random_placeholder

SKIPPABLE_EVENT_TYPES = {
    "response.function_call_arguments.delta",
    "response.created",
    "response.in_progress",
    "response.completed",
    "response.output_item.added",
    "response.output_item.done",
    "response.content_part.added",
    "response.content_part.done",
    "function_call_output",
}
MAX_TOOL_LINES = 5


def get_text(value: Any) -> str:
    match value:
        case str(text):
            return text.strip()
        case list(parts):
            return "\n".join(
                text
                for part in parts
                if isinstance(part, dict)
                for text in [str(part.get("text") or "").strip()]
                if text
            )
        case _:
            return ""


def clip_lines(text: str, max_lines: int = MAX_TOOL_LINES) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join([*lines[: max_lines - 1], "..."])


def get_tool_call_text(name: str, arguments: str) -> str:
    if arguments.strip():
        try:
            arguments = json.dumps(json.loads(arguments), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            pass
        return clip_lines(f"{name}\n{arguments}")
    return name


def get_tool_text(item: ResponseInputItemParam) -> str | None:
    match item:
        case {"type": "function_call", "name": str(name), "arguments": str(arguments)}:
            return get_tool_call_text(name, arguments)
        case {
            "type": "web_search_call",
            "action": {"query": str(query)},
        }:
            return f"web search\n{query.strip()}" if query.strip() else "web search"
        case {"type": str(item_type)} if item_type.endswith("_call"):
            return item_type.replace("_", " ")
        case _:
            return None


def get_item_text(item: ResponseInputItemParam) -> str:
    if text := get_tool_text(item):
        return text
    match item:
        case {"type": "message", "content": content}:
            return get_text(content)
        case {"type": "reasoning", "summary": summary}:
            return get_text(summary)
        case {"type": "function_call_output"}:
            return ""
        case _:
            return ""


def get_event_text(event: Any) -> str | None:
    if isinstance(event, ResponseFunctionToolCallOutputItem):
        return get_text(event.output)

    match event.type:
        case "response.reasoning_summary_part.added":
            value = getattr(getattr(event, "part", None), "text", "")
        case (
            "response.reasoning_summary_part.done"
            | "response.reasoning_summary_text.done"
            | "response.reasoning_text.done"
            | "response.output_text.done"
        ):
            value = ""
        case (
            "response.reasoning_summary_text.delta"
            | "response.reasoning_text.delta"
            | "response.output_text.delta"
        ):
            value = getattr(event, "delta", "")
        case "response.function_call_arguments.done":
            return get_tool_call_text(
                str(getattr(event, "name", "") or ""),
                str(getattr(event, "arguments", "") or ""),
            )
        case "response.web_search_call.in_progress":
            value = "Web search"
        case "response.web_search_call.searching":
            value = "Web search\nsearching"
        case "response.web_search_call.completed":
            value = "Web search\ncompleted"
        case _:
            return None
    return value if isinstance(value, str) else ""


def get_safe_class_name(value: str) -> str:
    return value.replace(".", "-")


def get_event_classes(event_type: str, text: str | None) -> str:
    if text is None:
        return f"{get_safe_class_name(event_type)} unknown"
    if "reasoning" in event_type:
        return "thinking"
    if "web_search_call" in event_type or "function_call_arguments" in event_type:
        return "tool"
    if "output_text" in event_type:
        return "answer"
    return get_safe_class_name(event_type)


class FaltooChatApp(App[None]):
    CSS = """
    App {
        background: $background;
        color: $text;
    }

    Screen {
        layout: vertical;
        background: $background;
    }

    #shell {
        width: 1fr;
        max-width: 80;
        height: 1fr;
    }

    #transcript {
        height: 1fr;
        overflow-y: auto;
        padding: 1 2 0 2;
    }

    #composer {
        height: 6;
        margin: 1 2 2 2;
        padding: 0 1;
        background: $surface;
        border: tall $primary;
        color: $text;
    }

    Markdown {
        margin: 0 0 1 0;
        padding: 0 1;
        background: $surface;
        border-left: wide $panel;
        color: $text;
    }

    .user {
        background: $primary 15%;
        border-left: wide $primary;
        color: $text;
    }

    .thinking {
        background: $accent 12%;
        border-left: wide $accent;
        color: $text;
    }

    .tool {
        background: $warning 12%;
        border-left: wide $warning;
        color: $text;
    }

    .answer {
        background: $success 12%;
        border-left: wide $success;
        color: $text;
    }

    .unknown {
        background: $error 12%;
        border-left: wide $error;
        color: $text;
    }
    """

    def __init__(self, session: sessions.Session) -> None:
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="shell"):
                yield VerticalScroll(id="transcript")
                yield Composer(
                    id="composer",
                    text="",
                    soft_wrap=True,
                    show_line_numbers=False,
                    highlight_cursor_line=False,
                    placeholder=get_random_placeholder(),
                )

    async def on_mount(self) -> None:
        await self.load_messages()

    async def load_messages(self) -> None:
        messages_json = sessions.get_messages(self.session)
        transcript = self.query_one("#transcript", VerticalScroll)
        transcript.remove_children()
        blocks = []
        for message in messages_json["messages"]:
            if not isinstance(message, dict):
                continue
            text = get_item_text(message)
            if text:
                match message:
                    case {"type": "message", "role": "user"}:
                        classes = "user"
                    case {"type": "message"}:
                        classes = "answer"
                    case {"type": "reasoning"}:
                        classes = "thinking"
                    case {"type": "function_call"} | {"type": "function_call_output"}:
                        classes = "tool"
                    case _:
                        classes = ""
                blocks.append(Markdown(text, classes=classes))
        if not blocks:
            blocks = [
                Markdown(
                    "_No messages yet. The void is waiting._",
                    classes="thinking",
                )
            ]
        transcript.mount(*blocks)
        transcript.scroll_end(animate=False, immediate=True)

    async def handle_command(self, question: str) -> bool:
        match question:
            case "/tree":
                open_in_default_editor(sessions.get_messages_path(self.session))
                return True
            case "/reset":
                workspace = Path(sessions.get_messages(self.session)["workspace"])
                self.session = sessions.get_session(
                    chat_key=self.session[0],
                    session_id=str(uuid4()),
                    workspace=workspace,
                )
                await self.load_messages()
                return True
            case _:
                return False

    async def submit_message(self) -> None:
        composer = self.query_one("#composer", Composer)
        transcript = self.query_one("#transcript", VerticalScroll)
        question = composer.text.strip()
        if not question:
            return

        composer.load_text("")
        if await self.handle_command(question):
            return
        transcript.mount(Markdown(question, classes="user"))
        is_at_bottom = transcript.max_scroll_y - transcript.scroll_y <= 3  # noqa: PLR2004
        if is_at_bottom:
            transcript.scroll_end(animate=False, immediate=True)

        current_type = ""
        markdown: Markdown = Markdown("")
        async for event in sessions.get_answer_streaming(
            session=self.session,
            question=question,
        ):
            event_type = getattr(event, "type", None)
            if not isinstance(event_type, str) or event_type in SKIPPABLE_EVENT_TYPES:
                continue

            text = get_event_text(event)
            classes = get_event_classes(event_type, text)
            text = text if text is not None else f"Unknown type: {event_type}\n\n"

            if current_type != event_type:
                current_type = event_type
                markdown = Markdown("", classes=classes)
                transcript.mount(markdown)

            if text:
                markdown.append(text)

            is_done = (
                event_type == "function_call_output"
                or event_type.endswith(".done")
                or event_type.endswith(".completed")
            )
            if is_done:
                current_type = ""
                continue

            is_at_bottom = transcript.max_scroll_y - transcript.scroll_y <= 3  # noqa: PLR2004
            if is_at_bottom:
                transcript.scroll_end(animate=False, immediate=True)


class Composer(TextArea):
    BINDINGS = [
        Binding("enter", "composer_enter", "Submit", priority=True),
    ]
    BINDING_GROUP_TITLE = "Chat"

    # for type-checking
    app = getters.app(FaltooChatApp)

    def action_composer_enter(self) -> None:
        self.app.run_worker(self.app.submit_message(), exclusive=True)


def main() -> int:
    session_id = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        FaltooChatApp(
            session=sessions.get_session(
                chat_key=sessions.get_dir_chat_key(Path.cwd()),
                session_id=session_id,
                workspace=Path.cwd(),
            ),
        ).run()
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
