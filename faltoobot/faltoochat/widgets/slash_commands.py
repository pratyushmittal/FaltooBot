from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from textual.widgets import OptionList
from textual.widgets.option_list import Option

from faltoobot import sessions
from faltoobot.config import build_config, config_status_text
from faltoobot.faltoochat.terminal import open_in_default_editor
from faltoobot.session_utils import get_local_user_message_item

from .session_picker import SessionPicker
from .text_input_modal import TextInputModal

from ..slash_commands import SlashCommandStore

if TYPE_CHECKING:
    from faltoobot.faltoochat.app import Composer, FaltooChatApp


SLASH_COMMANDS = {
    "/name": "name the current session",
    "/reset": "start a fresh session",
    "/resume": "resume another session",
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
            case "/name":

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
                return True
            case "/tree":
                open_in_default_editor(app.session.messages_path)
                return True
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
                return True
            case "/resume":

                async def on_result(result: dict[str, str] | None) -> None:
                    if result is None:
                        app.focus_composer()
                        return
                    app.session = sessions.get_session(
                        chat_key=app.session.chat_key,
                        session_id=result["id"],
                    )
                    app.workspace = Path(
                        sessions.get_messages(app.session)["workspace"]
                    )
                    await app.load_messages()
                    await app.queue().refresh_queue()

                app.push_screen(
                    SessionPicker(chat_key=app.session.chat_key),
                    on_result,
                )
                return True
            case "/status":
                await app.show_local_answer(
                    config_status_text(
                        build_config(), sessions.get_last_usage(app.session)
                    )
                )
                return True
            case _:
                return False

    async def handle_text(
        self,
        text: str,
        attachments: list[sessions.Attachment],
    ) -> bool:
        composer = cast("Composer", self.app.query_one("#composer"))
        command = text.strip()
        if command in SLASH_COMMANDS:
            composer.load_text("")
            return await self._handle_builtin_command(command)
        if prompt := SLASH_COMMAND_STORE.commands().get(command):
            composer.load_text("")
            message_item = get_local_user_message_item(prompt.template, attachments)
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
