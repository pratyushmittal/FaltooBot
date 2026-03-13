from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_LABEL = "com.faltoobot.agent"


@dataclass(slots=True)
class Config:
    home: Path
    root: Path
    env_file: Path
    log_file: Path
    state_db: Path
    session_db: Path
    launch_agent: Path
    run_script: Path
    openai_api_key: str
    openai_model: str
    system_prompt: str
    trigger_prefix: str
    allow_groups: bool
    allowed_chats: set[str]
    max_history_messages: int
    max_output_chars: int
    max_output_tokens: int


def app_root() -> Path:
    return Path(os.environ.get("FALTOOBOT_HOME", Path.home() / ".faltoobot")).expanduser()


def env_file() -> Path:
    return app_root() / ".env"


def default_env_text(root: Path) -> str:
    return f"""# Copy values in here and then run `faltoobot auth`.
# This file is loaded automatically by `faltoobot run` and `faltoobot install`.

OPENAI_API_KEY=
FALTOOBOT_OPENAI_MODEL=gpt-4.1-mini
FALTOOBOT_TRIGGER_PREFIX=!ai
FALTOOBOT_ALLOW_GROUPS=false
FALTOOBOT_ALLOWED_CHATS=
FALTOOBOT_MAX_HISTORY_MESSAGES=12
FALTOOBOT_MAX_OUTPUT_CHARS=6000
FALTOOBOT_MAX_OUTPUT_TOKENS=700
FALTOOBOT_SYSTEM_PROMPT=You are Faltoobot, a concise and helpful AI assistant replying inside WhatsApp. Keep replies practical and readable on mobile.
# FALTOOBOT_HOME={root}
"""


def ensure_layout() -> Path:
    root = app_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_env_file() -> Path:
    root = ensure_layout()
    path = root / ".env"
    if not path.exists():
        path.write_text(default_env_text(root), encoding="utf-8")
    return path


def load_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        cleaned = value.strip()
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
            cleaned = cleaned[1:-1]
        values[key.strip()] = cleaned
    return values


def load_runtime_env() -> None:
    path = env_file()
    values = load_env(path)
    for key, value in values.items():
        os.environ.setdefault(key, value)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def normalize_chat(value: str) -> str:
    value = value.strip()
    if not value:
        return value
    if "@" in value:
        return value
    digits = "".join(ch for ch in value if ch.isdigit())
    return f"{digits}@s.whatsapp.net" if digits else value


def env_set(name: str) -> set[str]:
    value = os.environ.get(name, "")
    return {normalize_chat(part) for part in value.split(",") if normalize_chat(part)}


def build_config() -> Config:
    load_runtime_env()
    root = ensure_layout()
    return Config(
        home=Path.home(),
        root=root,
        env_file=root / ".env",
        log_file=root / "faltoobot.log",
        state_db=root / "state.db",
        session_db=root / "session.db",
        launch_agent=Path.home() / "Library" / "LaunchAgents" / f"{APP_LABEL}.plist",
        run_script=root / "run.sh",
        openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
        openai_model=os.environ.get("FALTOOBOT_OPENAI_MODEL", "gpt-4.1-mini").strip()
        or "gpt-4.1-mini",
        system_prompt=os.environ.get(
            "FALTOOBOT_SYSTEM_PROMPT",
            "You are Faltoobot, a concise and helpful AI assistant replying inside WhatsApp. Keep replies practical and readable on mobile.",
        ).strip(),
        trigger_prefix=os.environ.get("FALTOOBOT_TRIGGER_PREFIX", "!ai").strip(),
        allow_groups=env_bool("FALTOOBOT_ALLOW_GROUPS", False),
        allowed_chats=env_set("FALTOOBOT_ALLOWED_CHATS"),
        max_history_messages=max(1, env_int("FALTOOBOT_MAX_HISTORY_MESSAGES", 12)),
        max_output_chars=max(500, env_int("FALTOOBOT_MAX_OUTPUT_CHARS", 6000)),
        max_output_tokens=max(100, env_int("FALTOOBOT_MAX_OUTPUT_TOKENS", 700)),
    )
