import json
import os
import tomllib
from importlib.metadata import PackageNotFoundError, version as package_version
from dataclasses import dataclass
from pathlib import Path
from typing import Any

APP_LABEL = "com.faltoobot.agent"
MODEL_OPTIONS = ("gpt-5.5", "gpt-5.2", "gpt-5.1", "gpt-5.2-codex", "gpt-5.1-codex")
TRANSCRIPTION_MODEL_OPTIONS = ("gpt-4o-mini-transcribe", "gpt-4o-transcribe")
THINKING_OPTIONS = ("none", "minimal", "low", "medium", "high", "xhigh")
DEFAULT_THINKING = "high"
GEMINI_MODEL = "gemini-3.1-flash-image-preview"


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
    openai_oauth: str
    openai_model: str
    openai_thinking: str
    openai_fast: bool
    openai_transcription_model: str
    allow_group_chats: set[str]
    allowed_chats: set[str]
    bot_name: str
    browser_binary: str
    gemini_api_key: str = ""
    gemini_model: str = GEMINI_MODEL


def app_root() -> Path:
    return Path.home() / ".faltoobot"


def default_config() -> dict[str, dict[str, Any]]:
    return {
        "openai": {
            "api_key": "",
            "oauth": "",
            "model": MODEL_OPTIONS[0],
            "thinking": DEFAULT_THINKING,
            "fast": False,
            "transcription_model": TRANSCRIPTION_MODEL_OPTIONS[1],
        },
        "gemini": {"gemini_api_key": "", "model": GEMINI_MODEL},
        "ui": {"theme": ""},
        "browser": {"binary": None},
        "bot": {
            "allow_group_chats": [],
            "allowed_chats": [],
            "bot_name": "Faltoo",
        },
    }


def merge_config(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    defaults = default_config()
    openai = as_dict(data.get("openai"))
    gemini = as_dict(data.get("gemini"))
    ui = as_dict(data.get("ui"))
    browser = as_dict(data.get("browser"))
    bot = as_dict(data.get("bot"))
    return {
        "openai": {
            "api_key": as_str(openai.get("api_key"), defaults["openai"]["api_key"]),
            "oauth": as_str(openai.get("oauth"), defaults["openai"]["oauth"]),
            "model": as_str(openai.get("model"), defaults["openai"]["model"]),
            "thinking": as_str(openai.get("thinking"), defaults["openai"]["thinking"]),
            "fast": as_bool(openai.get("fast"), defaults["openai"]["fast"]),
            "transcription_model": as_choice(
                openai.get("transcription_model"),
                defaults["openai"]["transcription_model"],
                TRANSCRIPTION_MODEL_OPTIONS,
            ),
        },
        "gemini": {
            "gemini_api_key": as_str(
                gemini.get("gemini_api_key"), defaults["gemini"]["gemini_api_key"]
            ),
            "model": as_str(gemini.get("model"), defaults["gemini"]["model"]),
        },
        "ui": {"theme": as_str(ui.get("theme"), defaults["ui"]["theme"])},
        "browser": {
            "binary": as_str(browser.get("binary"), ""),
        },
        "bot": {
            "allow_group_chats": sorted(as_chat_set(bot.get("allow_group_chats"))),
            "allowed_chats": sorted(as_chat_set(bot.get("allowed_chats"))),
            "bot_name": as_str(bot.get("bot_name"), defaults["bot"]["bot_name"]),
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
    data = merge_config(data)
    bot = data["bot"]
    openai = data["openai"]
    gemini = data["gemini"]
    ui = data["ui"]
    browser = data["browser"]
    allow_group_chats = (
        bot["allow_group_chats"] if isinstance(bot["allow_group_chats"], list) else []
    )
    allowed_group = ", ".join(
        quote(chat) for chat in allow_group_chats if isinstance(chat, str)
    )
    allowed_chats = (
        bot["allowed_chats"] if isinstance(bot["allowed_chats"], list) else []
    )
    allowed = ", ".join(quote(chat) for chat in allowed_chats if isinstance(chat, str))
    return "\n".join(
        [
            "# Faltoobot config",
            "",
            "[openai]",
            f"api_key = {quote(str(openai['api_key']))}",
            f"oauth = {quote(str(openai['oauth']))}",
            f"model = {quote(str(openai['model']))}",
            f"thinking = {quote(str(openai['thinking']))}",
            f"fast = {str(bool(openai['fast'])).lower()}",
            f"transcription_model = {quote(str(openai['transcription_model']))}",
            "",
            "[gemini]",
            f"gemini_api_key = {quote(str(gemini['gemini_api_key']))}",
            f"model = {quote(str(gemini['model']))}",
            "",
            "[ui]",
            f"theme = {quote(str(ui['theme']))}",
            "",
            "[browser]",
            f"binary = {quote(str(browser['binary']))}",
            "",
            "[bot]",
            f"allow_group_chats = [{allowed_group}]",
            f"allowed_chats = [{allowed}]",
            f"bot_name = {quote(str(bot['bot_name']))}",
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


def as_choice(value: Any, default: str, options: tuple[str, ...]) -> str:
    selected = as_str(value, default)
    return selected if selected in options else default


def as_int(value: Any, default: int, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return default
    return max(minimum, value)


def normalize_chat(value: str) -> str:
    """Return a stable WhatsApp chat key.

    Examples:
        ``"15551234567:42@s.whatsapp.net"`` -> ``"15551234567@s.whatsapp.net"``
        ``"+1 (555) 123-4567"`` -> ``"15551234567@s.whatsapp.net"``
    """
    value = value.strip()
    if not value:
        return value
    if "@" in value:
        user, _, server = value.partition("@")
        base, sep, device = user.rpartition(":")
        clean_user = base if sep and device.isdigit() else user
        return f"{clean_user}@{server}"
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
    browser = data["browser"]
    gemini = data["gemini"]
    return Config(
        home=Path.home(),
        root=root,
        config_file=path,
        log_file=root / "faltoobot.log",
        sessions_dir=root / "sessions",
        session_db=root / "session.db",
        launch_agent=Path.home() / "Library" / "LaunchAgents" / f"{APP_LABEL}.plist",
        run_script=root / "run.sh",
        openai_api_key=str(openai["api_key"]) or os.environ.get("OPENAI_API_KEY", ""),
        openai_oauth=str(openai["oauth"]),
        openai_model=str(openai["model"]),
        openai_thinking=str(openai["thinking"]),
        openai_fast=bool(openai["fast"]),
        openai_transcription_model=str(openai["transcription_model"]),
        allow_group_chats=set(str(chat) for chat in bot["allow_group_chats"]),
        allowed_chats=set(str(chat) for chat in bot["allowed_chats"]),
        bot_name=str(bot["bot_name"]),
        browser_binary=str(browser["binary"]),
        gemini_api_key=str(gemini["gemini_api_key"])
        or os.environ.get("GEMINI_API_KEY", ""),
        gemini_model=str(gemini["model"]),
    )


def _render_config_status_value(key: str, value: Any) -> str | bool | list[str]:
    if isinstance(value, str):
        rendered = value.strip()
        if (key.endswith("_api_key") or key.endswith("_oauth")) and rendered:
            return "<set>"
        return rendered
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, str)]
    return value


def _session_status_lines(
    session_id: str | None,
    workspace: Path | str | None,
) -> list[str]:
    lines: list[str] = []
    if session_id is not None:
        lines.append(f"• session_id={json.dumps(session_id)}")
    if workspace is not None:
        lines.append(f"• workspace={json.dumps(str(workspace))}")
    return lines


def config_status_text(
    config: Config,
    last_usage: dict[str, Any] | None = None,
    *,
    session_id: str | None = None,
    workspace: Path | str | None = None,
) -> str:
    try:
        version_text = package_version("faltoobot")
    except PackageNotFoundError:
        version_text = "unknown"

    data = merge_config(load_toml(config.config_file))
    entries: list[tuple[str, Any]] = []

    def add_entries(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                next_prefix = f"{prefix}_{child_key}" if prefix else child_key
                add_entries(next_prefix, child_value)
            return
        entries.append((prefix, value))

    add_entries("", data)

    lines = ["Faltoobot status", "", f"Version: {version_text}"]
    session_lines = _session_status_lines(session_id, workspace)
    if session_lines:
        lines.extend(["", "Session", *session_lines])
    lines.extend(["", "Config status"])
    for key, value in entries:
        lines.append(f"• {key}={json.dumps(_render_config_status_value(key, value))}")
    if last_usage is not None:
        lines.extend(
            [
                "",
                "Session usage",
                f"• last_usage={json.dumps(last_usage)}",
            ]
        )
    return "\n".join(lines)


def save_textual_theme(theme: str) -> None:
    path = ensure_config_file()
    data = merge_config(load_toml(path))
    data["ui"]["theme"] = theme.strip()
    rendered = render_config(data)
    if path.read_text(encoding="utf-8") == rendered:
        return
    path.write_text(rendered, encoding="utf-8")


def load_textual_theme() -> str:
    data = merge_config(load_toml(ensure_config_file()))
    return as_str(data["ui"].get("theme"), "")
