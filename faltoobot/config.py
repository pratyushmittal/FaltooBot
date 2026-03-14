import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

APP_LABEL = "com.faltoobot.agent"
MODEL_OPTIONS = ("gpt-5.2", "gpt-5.1", "gpt-5.2-codex", "gpt-5.1-codex")
DEFAULT_SYSTEM_PROMPT = (
    "You are Faltoobot, a concise and helpful AI assistant replying inside WhatsApp. "
    "Keep replies practical and readable on mobile."
)


@dataclass(slots=True)
class Config:
    home: Path
    root: Path
    config_file: Path
    log_file: Path
    sessions_dir: Path
    session_db: Path
    launch_agent: Path
    run_script: Path
    openai_api_key: str
    openai_model: str
    system_prompt: str
    allow_groups: bool
    allowed_chats: set[str]
    max_history_messages: int


def app_root() -> Path:
    return Path.home() / ".faltoobot"


def default_config() -> dict[str, dict[str, Any]]:
    return {
        "openai": {
            "api_key": "",
            "model": MODEL_OPTIONS[0],
        },
        "bot": {
            "allow_groups": False,
            "allowed_chats": [],
            "max_history_messages": 12,
            "system_prompt": DEFAULT_SYSTEM_PROMPT,
        },
    }


def merge_config(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    defaults = default_config()
    openai = as_dict(data.get("openai"))
    bot = as_dict(data.get("bot"))
    return {
        "openai": {
            "api_key": as_str(openai.get("api_key"), defaults["openai"]["api_key"]),
            "model": as_str(openai.get("model"), defaults["openai"]["model"]),
        },
        "bot": {
            "allow_groups": as_bool(bot.get("allow_groups"), defaults["bot"]["allow_groups"]),
            "allowed_chats": sorted(as_chat_set(bot.get("allowed_chats"))),
            "max_history_messages": as_int(
                bot.get("max_history_messages"),
                defaults["bot"]["max_history_messages"],
                1,
            ),
            "system_prompt": as_str(bot.get("system_prompt"), defaults["bot"]["system_prompt"]),
        },
    }


def ensure_layout() -> Path:
    root = app_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_config_file() -> Path:
    path = ensure_layout() / "config.toml"
    migrate_config_file(path)
    return path


def load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as file:
        data = tomllib.load(file)
    return as_dict(data)


def quote(value: str) -> str:
    return json.dumps(value)


def render_config(data: dict[str, dict[str, Any]]) -> str:
    bot = data["bot"]
    openai = data["openai"]
    allowed_chats = bot["allowed_chats"] if isinstance(bot["allowed_chats"], list) else []
    allowed = ", ".join(quote(chat) for chat in allowed_chats if isinstance(chat, str))
    return "\n".join(
        [
            "# Faltoobot config",
            "",
            "[openai]",
            f"api_key = {quote(str(openai['api_key']))}",
            f"model = {quote(str(openai['model']))}",
            "",
            "[bot]",
            f"allow_groups = {str(bool(bot['allow_groups'])).lower()}",
            f"allowed_chats = [{allowed}]",
            f"max_history_messages = {int(bot['max_history_messages'])}",
            f"system_prompt = {quote(str(bot['system_prompt']))}",
            "",
        ]
    )


def migrate_config_file(path: Path) -> bool:
    text = render_config(merge_config(load_toml(path)))
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_str(value: Any, default: str) -> str:
    if not isinstance(value, str):
        return default
    cleaned = value.strip()
    return cleaned or default


def as_bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def as_int(value: Any, default: int, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return default
    return max(minimum, value)


def normalize_chat(value: str) -> str:
    value = value.strip()
    if not value:
        return value
    if "@" in value:
        return value
    digits = "".join(char for char in value if char.isdigit())
    return f"{digits}@s.whatsapp.net" if digits else value


def as_chat_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    chats = {normalize_chat(item) for item in value if isinstance(item, str)}
    return {chat for chat in chats if chat}


def build_config() -> Config:
    root = ensure_layout()
    path = ensure_config_file()
    data = merge_config(load_toml(path))
    openai = data["openai"]
    bot = data["bot"]
    return Config(
        home=Path.home(),
        root=root,
        config_file=path,
        log_file=root / "faltoobot.log",
        sessions_dir=root / "sessions",
        session_db=root / "session.db",
        launch_agent=Path.home() / "Library" / "LaunchAgents" / f"{APP_LABEL}.plist",
        run_script=root / "run.sh",
        openai_api_key=as_str(openai.get("api_key"), os.environ.get("OPENAI_API_KEY", "")),
        openai_model=as_str(openai.get("model"), MODEL_OPTIONS[0]),
        system_prompt=as_str(bot.get("system_prompt"), DEFAULT_SYSTEM_PROMPT),
        allow_groups=as_bool(bot.get("allow_groups"), False),
        allowed_chats=as_chat_set(bot.get("allowed_chats")),
        max_history_messages=as_int(bot.get("max_history_messages"), 12, 1),
    )
