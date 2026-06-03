import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias, cast

from faltoobot.config import Config
from faltoobot.sessions import LAST_USED_FILE, MESSAGES_FILE

MessageItem: TypeAlias = dict[str, Any]
MessageHistory: TypeAlias = list[MessageItem]

MISSING_FUNCTION_CALL_OUTPUT = "Tool call failed before output was saved."
MIN_QUOTED_LENGTH = 2
CRON_COMMAND_FIELDS = 6


CRON_LOG_ERROR_PATTERNS: tuple[tuple[str, str], ...] = (
    ("missing interpreter/path", r"Missing Python interpreter|No such file or directory"),
    ("browser startup failure", r"CDP port 9222|browser did not become ready"),
    ("python traceback", r"Traceback \(most recent call last\)|RuntimeError|Exception"),
    ("HTTP/service error", r"\bHTTP/1\.1\"?\s+(?:429|500|502|503)\b|Internal Server Error|Bad Gateway"),
)
CRON_LINE_RE = re.compile(r"\bcd\s+(?P<cwd>\"[^\"]+\"|'[^']+'|\S+)\s+&&\s+(?P<cmd>.*)")
ABSOLUTE_HOME_RE = re.compile(r"/home/[A-Za-z0-9._-]+/")


@dataclass(slots=True, frozen=True)
class CronHealthIssue:
    kind: str
    detail: str

    def render(self) -> str:
        return f"{self.kind}: {self.detail}"


def _strip_shell_quotes(value: str) -> str:
    value = value.strip()
    if (
        len(value) >= MIN_QUOTED_LENGTH
        and value[0] == value[-1]
        and value[0] in {'\"', "'"}
    ):
        return value[1:-1]
    return value


def _load_crontab_text() -> str:
    try:
        result = subprocess.run(
            ["crontab", "-l"], check=False, text=True, capture_output=True
        )
    except FileNotFoundError:
        return ""
    return result.stdout if result.returncode == 0 else ""


def _iter_cron_commands(crontab_text: str) -> list[tuple[int, Path | None, str]]:
    commands: list[tuple[int, Path | None, str]] = []
    for line_no, line in enumerate(crontab_text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("PATH="):
            continue
        match = CRON_LINE_RE.search(stripped)
        if match:
            commands.append((line_no, Path(_strip_shell_quotes(match.group("cwd"))), match.group("cmd")))
            continue
        parts = stripped.split(maxsplit=5)
        if len(parts) == CRON_COMMAND_FIELDS:
            commands.append((line_no, None, parts[5]))
    return commands


def _referenced_script(cwd: Path, command: str) -> Path | None:
    token = command.strip().split(maxsplit=1)[0] if command.strip() else ""
    if not token.startswith("./"):
        return None
    return cwd / token[2:]


def _script_home_references(script: Path, config: Config) -> list[str]:
    try:
        text = script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    current_home = config.home.as_posix().rstrip("/") + "/"
    stale = sorted({match.group(0) for match in ABSOLUTE_HOME_RE.finditer(text)})
    return [path for path in stale if path != current_home]


def _script_uses_local_venv_python(script: Path) -> bool:
    try:
        text = script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return ".venv/bin/python" in text


def _recent_log_error_summary(path: Path, *, max_bytes: int) -> list[str]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []

    summaries: list[str] = []
    for label, pattern in CRON_LOG_ERROR_PATTERNS:
        count = len(re.findall(pattern, text, flags=re.IGNORECASE))
        if count:
            summaries.append(f"{label} x{count}")
    return summaries


def _inspect_cron_command(
    *,
    config: Config,
    line_no: int,
    cwd: Path | None,
    command: str,
    seen_workdirs: set[Path],
) -> list[CronHealthIssue]:
    issues: list[CronHealthIssue] = []
    if cwd is None:
        return issues
    seen_workdirs.add(cwd)
    if not cwd.exists():
        return [
            CronHealthIssue(
                "cron", f"line {line_no} working directory is missing: {cwd}"
            )
        ]

    script = _referenced_script(cwd, command)
    if script is None:
        return issues
    if not script.exists():
        return [CronHealthIssue("cron", f"line {line_no} script is missing: {script}")]
    if not os.access(script, os.X_OK):
        issues.append(
            CronHealthIssue("cron", f"line {line_no} script is not executable: {script}")
        )
    for stale_home in _script_home_references(script, config):
        issues.append(
            CronHealthIssue(
                "cron",
                f"line {line_no} script references another home directory: {stale_home}",
            )
        )
    if _script_uses_local_venv_python(script):
        python_bin = cwd / ".venv" / "bin" / "python"
        if not os.access(python_bin, os.X_OK):
            issues.append(
                CronHealthIssue(
                    "cron",
                    f"line {line_no} uses a broken local venv interpreter: {python_bin}",
                )
            )
    return issues


def _recent_workdir_logs(workdir: Path, cutoff: float) -> list[Path]:
    if not workdir.exists():
        return []
    try:
        return [
            path
            for path in workdir.rglob("*.log")
            if path.is_file() and path.stat().st_mtime >= cutoff
        ]
    except OSError:
        return []


def _recent_root_logs(config: Config, cutoff: float) -> list[Path]:
    try:
        return [
            path
            for path in config.root.glob("*.log")
            if path.is_file() and path.stat().st_mtime >= cutoff
        ]
    except OSError:
        return []


def _inspect_recent_logs(
    config: Config,
    *,
    workdirs: set[Path],
    recent_seconds: int,
    max_log_bytes: int,
) -> list[CronHealthIssue]:
    cutoff = time.time() - recent_seconds
    log_paths: list[Path] = []
    for workdir in sorted(workdirs):
        log_paths.extend(_recent_workdir_logs(workdir, cutoff))
    log_paths.extend(_recent_root_logs(config, cutoff))

    issues: list[CronHealthIssue] = []
    for path in sorted(set(log_paths)):
        summaries = _recent_log_error_summary(path, max_bytes=max_log_bytes)
        if summaries:
            issues.append(
                CronHealthIssue(
                    "cron-log",
                    f"{path} has recent recurring errors: {', '.join(summaries)}",
                )
            )
    return issues


def inspect_cron_health(
    config: Config,
    *,
    crontab_text: str | None = None,
    recent_seconds: int = 7 * 24 * 60 * 60,
    max_log_bytes: int = 64 * 1024,
) -> list[CronHealthIssue]:
    """Return cron/workspace health issues without mutating running bot code.

    This intentionally reports broad failure classes (missing paths, stale home paths,
    broken local virtualenvs, and recent log error signatures) so operators can repair
    recurring monitors before they silently miss notifications.
    """
    issues: list[CronHealthIssue] = []
    text = _load_crontab_text() if crontab_text is None else crontab_text

    seen_workdirs: set[Path] = set()
    for line_no, cwd, command in _iter_cron_commands(text):
        issues.extend(
            _inspect_cron_command(
                config=config,
                line_no=line_no,
                cwd=cwd,
                command=command,
                seen_workdirs=seen_workdirs,
            )
        )
    issues.extend(
        _inspect_recent_logs(
            config,
            workdirs=seen_workdirs,
            recent_seconds=recent_seconds,
            max_log_bytes=max_log_bytes,
        )
    )
    return issues


def _call_id(item: MessageItem, item_type: str) -> str | None:
    if item.get("type") != item_type:
        return None
    call_id = item.get("call_id")
    return call_id if isinstance(call_id, str) and call_id else None


def _missing_function_call_output(call_id: str) -> MessageItem:
    return {
        "id": f"fco_{call_id}",
        "type": "function_call_output",
        "call_id": call_id,
        "output": MISSING_FUNCTION_CALL_OUTPUT,
        "status": "completed",
    }


def ensure_function_call_outputs(items: MessageHistory) -> bool:
    """Mutate history so every function_call has a non-null output item."""
    output_ids = {
        call_id
        for item in items
        if (call_id := _call_id(item, "function_call_output"))
        and item.get("output") is not None
    }
    fixed: MessageHistory = []
    pending: list[str] = []
    changed = False

    for item in items:
        output_call_id = _call_id(item, "function_call_output")
        if output_call_id:
            item = dict(item)
            if item.get("output") is None:
                # comment: Responses treats null output as not answering the call.
                item.update(output=MISSING_FUNCTION_CALL_OUTPUT, status="completed")
                changed = True
            if output_call_id in pending:
                pending.remove(output_call_id)
            fixed.append(item)
            continue

        call_id = _call_id(item, "function_call")
        if call_id and call_id not in output_ids and call_id not in pending:
            pending.append(call_id)
        elif not call_id and pending:
            fixed.extend(_missing_function_call_output(call_id) for call_id in pending)
            pending.clear()
            changed = True
        fixed.append(item)

    if pending:
        fixed.extend(_missing_function_call_output(call_id) for call_id in pending)
        changed = True
    if changed:
        items[:] = fixed
    return changed


def _last_used_available(chat_root: Path) -> bool:
    path = chat_root / LAST_USED_FILE
    if not path.exists():
        return False
    try:
        session_id = path.read_text(encoding="utf-8").strip()
    except OSError:
        # comment: unreadable marker should be rebuilt from current session mtimes.
        return False
    if not session_id or session_id in {".", ".."} or "/" in session_id:
        # comment: corrupt marker should not be trusted.
        return False
    return (chat_root / session_id / MESSAGES_FILE).exists()


def _latest_session_id(chat_root: Path) -> str | None:
    message_paths = list(chat_root.glob(f"*/{MESSAGES_FILE}"))
    if not message_paths:
        return None
    message_paths.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return message_paths[0].parent.name


def heal_last_used_files(config: Config) -> bool:
    sessions_dir = config.sessions_dir
    if not sessions_dir.exists():
        # comment: fresh installs do not have session roots to heal.
        return False

    changed = False
    for chat_root in sessions_dir.iterdir():
        if not chat_root.is_dir() or _last_used_available(chat_root):
            continue
        session_id = _latest_session_id(chat_root)
        if session_id is None:
            # comment: chats without any messages.json have no usable session.
            continue
        (chat_root / LAST_USED_FILE).write_text(f"{session_id}\n", encoding="utf-8")
        changed = True
    return changed


def heal_function_call_outputs(config: Config) -> bool:
    sessions_dir = config.sessions_dir
    if not sessions_dir.exists():
        # comment: fresh installs do not have histories to heal.
        return False

    changed = False
    for path in sessions_dir.rglob("messages.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # comment: leave unreadable/corrupt history files untouched during doctor runs.
            continue
        messages = data.get("messages") if isinstance(data, dict) else None
        if not isinstance(messages, list):
            # comment: skip old/corrupt session files that are not normal histories.
            continue
        if not ensure_function_call_outputs(cast(MessageHistory, messages)):
            continue

        stat = path.stat()
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        changed = True
    return changed


def main(config: Config) -> list[str]:
    changes: list[str] = []
    if heal_last_used_files(config):
        changes.append("doctor:heal-last-used")
    if heal_function_call_outputs(config):
        changes.append("doctor:heal-function-call-outputs")
    return changes
