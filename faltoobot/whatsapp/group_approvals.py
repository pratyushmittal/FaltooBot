import json
from datetime import UTC, datetime, timedelta
from logging import Logger
from typing import Any

from neonize.proto import Neonize_pb2
from neonize.utils.jid import build_jid

from faltoobot.config import (
    Config,
    load_toml,
    merge_config,
    normalize_chat,
    render_config,
)

from .allowlist import matches_allowed_chats

REQUEST_NOTIFY_COOLDOWN = timedelta(hours=1)
APPROVED = "approved"
DENIED = "denied"
PENDING = "pending"
APPROVAL_COMMANDS = {"/approve_group", "/deny_group", "/groups"}
COMMAND_ARG_COUNT = 2

Approval = dict[str, str]


def _approvers(config: Config) -> set[str]:
    return {chat for chat in config.allowed_chats if not chat.endswith("@g.us")}


def _load(config: Config) -> dict[str, Approval]:
    path = config.root / "group_approvals.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        normalize_chat(group): {str(key): str(value) for key, value in record.items()}
        for group, record in data.items()
        if isinstance(group, str) and isinstance(record, dict)
    }


def _save(config: Config, approvals: dict[str, Approval]) -> None:
    path = config.root / "group_approvals.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    temp_path.write_text(
        json.dumps(approvals, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temp_path.replace(path)


def _timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _should_notify(record: Approval | None) -> bool:
    if record is None:
        return True
    if record.get("status") != PENDING:
        return False
    try:
        last_notified = datetime.fromisoformat(
            record.get("last_notified_at", "").replace("Z", "+00:00")
        )
    except ValueError:
        return True
    return datetime.now(UTC) - last_notified >= REQUEST_NOTIFY_COOLDOWN


async def _group_title(
    client: Any, group: Neonize_pb2.JID, fallback: str, logger: Logger
) -> str:
    try:
        info = await client.get_group_info(group)
    except Exception:
        logger.debug("Failed to fetch WhatsApp group name", exc_info=True)
        return fallback
    name = getattr(info, "GroupName", None)
    topic = getattr(info, "GroupTopic", None)
    title = str(
        getattr(name, "Name", "")
        or getattr(topic, "Topic", "")
        or getattr(info, "Name", "")
        or getattr(info, "Topic", "")
        or ""
    ).strip()
    return title or fallback


def _request_text(record: Approval) -> str:
    group_jid = record.get("group_jid", "")
    lines = [
        "Group approval requested",
        "",
        f"Group: {record.get('group_name') or group_jid}",
        f"JID: {group_jid}",
        f"Requested by: {record.get('requested_by_name') or record.get('requested_by') or '<unknown>'}",
    ]
    if record.get("requested_by"):
        lines.append(f"Requester JID: {record['requested_by']}")
    if record.get("message"):
        lines.extend(["", f"Message: {record['message']}"])
    lines.extend(
        [
            "",
            "Reply with:",
            f"/approve_group {group_jid}",
            f"/deny_group {group_jid}",
        ]
    )
    return "\n".join(lines)


def _chat_jid(chat: str) -> Neonize_pb2.JID | None:
    user, separator, server = chat.partition("@")
    if not separator or not user or not server:
        return None
    return build_jid(user, server)


async def request_approval(  # noqa: PLR0913
    client: Any,
    *,
    config: Config,
    group: Neonize_pb2.JID,
    group_jid: str,
    sender_jid: str,
    sender_name: str | None,
    message: str,
    logger: Logger,
) -> None:
    """Persist a group approval request and DM all allowed approvers."""
    approvers = _approvers(config)
    if not approvers:
        logger.info(
            "No group approvers configured for approval request from %s", group_jid
        )
        return

    approvals = _load(config)
    group_id = normalize_chat(group_jid)
    current = approvals.get(group_id)
    if not _should_notify(current):
        return

    timestamp = _timestamp()
    record: Approval = {
        "status": PENDING,
        "group_jid": group_id,
        "group_name": await _group_title(client, group, group_id, logger),
        "requested_by": normalize_chat(sender_jid),
        "requested_by_name": sender_name or sender_jid,
        "message": message,
        "requested_at": current.get("requested_at", timestamp)
        if current
        else timestamp,
        "last_notified_at": timestamp,
    }
    approvals[group_id] = record
    _save(config, approvals)

    text = _request_text(record)
    for approver in sorted(approvers):
        approver_jid = _chat_jid(approver)
        if approver_jid is None:
            continue
        try:
            await client.send_message(approver_jid, text)
        except Exception:
            logger.exception("Failed to send group approval request to %s", approver)


def _write_group_allowlist(config: Config, groups: set[str]) -> None:
    data = merge_config(load_toml(config.config_file))
    data["bot"]["allow_group_chats"] = sorted(groups)
    config.config_file.parent.mkdir(parents=True, exist_ok=True)
    config.config_file.write_text(render_config(data), encoding="utf-8")


def _groups_text(config: Config) -> str:
    approvals = _load(config)
    lines = ["WhatsApp groups", ""]
    lines.append("Approved:")
    lines.extend(
        [f"• {group}" for group in sorted(config.allow_group_chats)] or ["• none"]
    )
    lines.extend(["", "Pending:"])
    pending = {
        group: record
        for group, record in approvals.items()
        if record.get("status") == PENDING
    }
    if not pending:
        lines.append("• none")
        return "\n".join(lines)
    for group, record in sorted(pending.items()):
        lines.append(f"• {record.get('group_name') or group} — {group}")
    return "\n".join(lines)


def _update_status(
    config: Config, *, group_id: str, status: str, decided_by: str
) -> None:
    approvals = _load(config)
    record = approvals.get(group_id, {"group_jid": group_id})
    record["status"] = status
    record["decided_at"] = _timestamp()
    record["decided_by"] = decided_by
    approvals[group_id] = record
    _save(config, approvals)


async def handle_command(  # noqa: PLR0911
    client: Any,
    *,
    config: Config,
    event: Any,
    prompt: str,
    source_ids: set[str],
) -> bool:
    parts = prompt.strip().split(maxsplit=1)
    command = parts[0] if parts else ""
    if command not in APPROVAL_COMMANDS:
        return False
    if event.Info.MessageSource.IsGroup:
        await client.reply_message("Use this command in a direct chat with me.", event)
        return True

    approvers = _approvers(config)
    if not approvers:
        await client.reply_message(
            "No group approvers are configured. Add your number to bot.allowed_chats first.",
            event,
        )
        return True
    if not matches_allowed_chats(approvers, source_ids):
        await client.reply_message(
            "You are not allowed to approve WhatsApp groups.", event
        )
        return True
    if command == "/groups":
        await client.reply_message(_groups_text(config), event)
        return True

    group_id = normalize_chat(parts[1]) if len(parts) == COMMAND_ARG_COUNT else ""
    if not group_id.endswith("@g.us"):
        await client.reply_message(f"Usage: {command} <group_jid>", event)
        return True

    approver = sorted(source_ids)[0] if source_ids else "<unknown>"
    if command == "/approve_group":
        config.allow_group_chats.add(group_id)
        _write_group_allowlist(config, config.allow_group_chats)
        _update_status(config, group_id=group_id, status=APPROVED, decided_by=approver)
        await client.reply_message(f"Approved group {group_id}.", event)
        return True

    _update_status(config, group_id=group_id, status=DENIED, decided_by=approver)
    await client.reply_message(f"Denied group {group_id}.", event)
    return True
