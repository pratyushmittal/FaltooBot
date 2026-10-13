from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from textual.widgets import OptionList
from textual.widgets.option_list import Option

from faltoobot import handoff, post_response_hooks, sessions
from faltoobot.config import BACKEND_OPTIONS, build_config, config_status_text
from faltoobot.faltoochat.terminal import open_in_default_editor
from faltoobot.session_utils import get_local_user_message_item

from .session_picker import SessionPicker
from .telescope import Telescope
from .text_input_modal import TextInputModal

from ..slash_commands import SlashCommandStore

if TYPE_CHECKING:
    from faltoobot.faltoochat.app import Composer, FaltooChatApp


SLASH_COMMANDS = {
    "/compact": "compact this session history",
    "/handoff": "hand this session to another backend",
    "/name": "name the current session",
    "/reset": "start a fresh session",
    "/resume": "resume another session",
    "/run-hooks": "run hooks for git changes",
    "/status": "show bot status",
    "/tree": "open the current session messages file",
}
SLASH_COMMAND_STORE = SlashCommandStore(frozenset(SLASH_COMMANDS))


async def _rename_session(app: "FaltooChatApp", name: str) -> None:
    try:
        sessions.set_session_name(app.session, name)
    except (OSError, ValueError) as error:
        app.notify(
            f"Could not rename session: {error}",
            severity="error",
        )
        return
    await app.show_local_answer(
        f"`Saved session name: {name}`" if name else "`Cleared session name.`"
    )


async def _hand_off(app: "FaltooChatApp", target: str) -> None:
    if target not in BACKEND_OPTIONS:
        await app.show_local_answer(f"`Usage: /handoff <{'|'.join(BACKEND_OPTIONS)}>`")
        return
    if app.is_answering:
        app.notify("Wait for the current answer to finish.", severity="warning")
        return
    await app.show_local_answer(f"`Handing this session over to {target}...`")
    try:
        result = await handoff.hand_off(app.session.chat_key, to=target)
    except Exception as error:
        await app.show_local_answer(f"`Hand-off failed: {error}`")
        return
    app.session = cast(sessions.Session, result.session)
    await app.load_messages()
    await app.show_local_answer(
        f"{result.verification}\n\n`Hand-off document: {result.document}`"
    )


def _open_name_modal(app: "FaltooChatApp") -> None:
    async def on_result(name: str | None) -> None:
        if name is not None:
            await _rename_session(app, name)
        app.focus_composer()

    app.push_screen(
        TextInputModal(
            initial_value=app.session.session_id,
            title="Name session",
            placeholder="Enter a session name",
            allow_empty=True,
        ),
        on_result,
    )


def _open_run_hooks_picker(app: "FaltooChatApp") -> None:
    async def on_result(scope: post_response_hooks.HookDiffScope | None) -> None:
        if scope is not None:
            app.is_answering = True
            app.refresh_terminal_title()
            app.active_stream_worker = app.run_worker(
                app._start_streaming(
                    app.transcript,
                    against=scope,
                ),
                exclusive=True,
            )
        app.focus_composer()

    app.push_screen(
        Telescope(
            items=list(post_response_hooks.HookDiffScope),
            title="Run post-response hooks",
            placeholder="Choose git changes to send to hooks",
        ),
        on_result,
    )


def _open_resume_picker(app: "FaltooChatApp") -> None:
    async def on_result(result: dict[str, str] | None) -> None:
        if result is None:
            app.focus_composer()
            return
        transcript = app.query_one("#transcript")
        transcript.loading = True
        try:
            app.session = sessions.get_session(
                chat_key=app.session.chat_key,
                session_id=result["id"],
            )
            app.workspace = Path(sessions.get_messages(app.session)["workspace"])
            await app.load_recent_messages()
            await app.queue().refresh_queue()
        finally:
            transcript.loading = False

    app.push_screen(
        SessionPicker(chat_key=app.session.chat_key),
        on_result,
    )


class SlashCommandsOptionList(OptionList):
    """Suggestion list for built-in and saved slash commands under the composer."""

    def _command_descriptions(self) -> dict[str, str]:
        descriptions = dict(SLASH_COMMANDS)
        for command, prompt in SLASH_COMMAND_STORE.commands().items():
            descriptions[command] = prompt.preview
        return descriptions

    def show_matches_for(self, text: str) -> None:
        if not text.startswith("/"):
            self.hide_commands()
            return
        descriptions = self._command_descriptions()
        commands = [command for command in descriptions if command.startswith(text)]
        self.clear_options()
        self.display = bool(commands)
        if not commands:
            return
        self.add_options(
            Option(f"{command} — {descriptions[command]}") for command in commands
        )
        self.highlighted = 0

    def hide_commands(self) -> None:
        self.clear_options()
        self.display = False

    def _command_for_index(self, index: int) -> str | None:
        if not (0 <= index < len(self.options)):
            return None
        prompt = str(self.options[index].prompt)
        command, _separator, _description = prompt.partition(" — ")
        return command or None

    def selected_completion(self, text: str) -> str | None:
        if not self.display or self.highlighted is None or not text.startswith("/"):
            return None
        command = self._command_for_index(self.highlighted)
        return None if command in {None, text} else command

    async def _handle_builtin_command(self, command: str) -> bool:
        app = cast("FaltooChatApp", self.app)

        match command:
            case "/compact":
                if app.is_answering:
                    # comment: compacting mid-answer would race the running turn.
                    app.notify(
                        "Wait for the current answer to finish.", severity="warning"
                    )
                    return True
                compacted = await sessions.compact_message_history(app.session)
                await app.load_messages()
                await app.show_local_answer(
                    "`Memory compacted.`" if compacted else "`Nothing to compact.`"
                )
            case "/name":
                _open_name_modal(app)
            case "/tree":
                open_in_default_editor(
                    sessions.get_messages_path_or_claude_transcript(app.session)
                )
            case "/reset":
                workspace = app.workspace
                app.session = sessions.get_session(
                    chat_key=app.session.chat_key,
                    session_id=str(uuid4()),
                    workspace=workspace,
                )
                app.workspace = workspace
                await app.load_messages()
                await app.queue().refresh_queue()
            case "/resume":
                _open_resume_picker(app)
            case "/run-hooks":
                _open_run_hooks_picker(app)
            case "/status":
                await app.show_local_answer(
                    config_status_text(
                        build_config(),
                        sessions.get_last_usage(app.session),
                        session_id=app.session.session_id,
                        workspace=app.workspace,
                        backend=sessions.get_backend(
                            sessions.get_messages(app.session)
                        ),
                    )
                )
            case _:
                return False
        return True

    async def handle_text(
        self,
        text: str,
        attachments: list[sessions.Attachment],
    ) -> bool:
        composer = cast("Composer", self.app.query_one("#composer"))
        command, _separator, args_text = text.strip().partition(" ")
        if command == "/handoff":
            composer.load_text("")
            await _hand_off(cast("FaltooChatApp", self.app), args_text.strip())
            return True
        if command in SLASH_COMMANDS and not args_text:
            composer.load_text("")
            return await self._handle_builtin_command(command)
        message = SLASH_COMMAND_STORE.get_prompt_message(command, args_text)
        if message is not None:
            composer.load_text("")
            message_item = get_local_user_message_item(message, attachments)
            await cast("FaltooChatApp", self.app).handle_message(message_item)
            return True
        return False

    async def on_option_list_option_selected(
        self,
        event: OptionList.OptionSelected,
    ) -> None:
        """Complete the current turn from the selected slash-command suggestion."""
        event.stop()
        if (command := self._command_for_index(event.option_index)) is None:
            return
        composer = cast("Composer", self.app.query_one("#composer"))
        await self.handle_text(command, composer.take_attachments())
