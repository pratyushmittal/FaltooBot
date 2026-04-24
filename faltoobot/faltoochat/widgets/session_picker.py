from .telescope import Telescope


class SessionPicker(Telescope[dict[str, str]]):
    def __init__(self, *, chat_key: str) -> None:
        from faltoobot import sessions

        super().__init__(
            items=sessions.list_sessions(chat_key),
            title="Resume session",
            placeholder="Type a session name or id",
        )
