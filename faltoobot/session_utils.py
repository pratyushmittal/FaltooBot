from pathlib import Path
from typing import Any

from faltoobot import sessions
from faltoobot.gpt_utils import MessageItem


def get_local_user_message_item(
    question: str,
    attachments: list[sessions.Attachment],
) -> MessageItem:
    # comment: this local MessageItem is not appended to messages_json. It mirrors the
    # user item that later goes through session handling, where local file attachments
    # are uploaded before the API call.
    content: list[dict[str, Any]] = [
        *([{"type": "input_text", "text": question}] if question else []),
        *({"type": "input_image", "image_path": str(path)} for path in attachments),
    ]
    return {
        "type": "message",
        "role": "user",
        "content": content,
    }


def decompose_local_message_item(
    message_item: MessageItem,
) -> tuple[str, list[sessions.Attachment]]:
    question = ""
    attachments: list[sessions.Attachment] = []
    content = message_item.get("content")
    if isinstance(content, str):
        return content, attachments
    if not isinstance(content, list):
        return question, attachments
    for part in content:
        if part.get("type") == "input_text":
            question += str(part.get("text") or "")
        if part.get("type") == "input_image" and isinstance(
            part.get("image_path"), str
        ):
            attachments.append(Path(part["image_path"]))
    return question, attachments
