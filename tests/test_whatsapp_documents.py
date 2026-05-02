from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message

from faltoobot.whatsapp import runtime


class FakeMessage:
    def __init__(self) -> None:
        self.documentMessage = SimpleNamespace(
            fileName="../Report Q1.pdf",
            title="",
            mimetype="application/pdf",
            caption="",
        )

    def HasField(self, name: str) -> bool:
        return name == "documentMessage"


class FakeClient:
    async def download_any(self, message):
        return b"pdf bytes"


@pytest.mark.anyio
async def test_save_document_attachment_saves_under_documents(tmp_path: Path) -> None:
    path = await runtime.save_document_attachment(
        cast(Any, FakeClient()),
        FakeMessage(),
        workspace=tmp_path,
        message_id="abc/123",
    )

    assert path == tmp_path / "documents" / "Report-Q1.pdf"
    assert path.read_bytes() == b"pdf bytes"


def test_document_with_caption_message_reads_caption_without_context_error() -> None:
    message = Message()
    document = message.documentWithCaptionMessage.message.documentMessage
    document.fileName = "report.pdf"
    document.caption = "summarize this"

    assert runtime._message_text(message) == "summarize this"
    assert runtime._message_context_info(message) is None


def test_document_with_caption_message_reads_nested_context_info() -> None:
    message = Message()
    document = message.documentWithCaptionMessage.message.documentMessage
    document.fileName = "report.pdf"
    document.contextInfo.quotedMessage.conversation = "previous question"

    context = runtime._message_context_info(message)

    assert context is not None
    assert context.quotedMessage.conversation == "previous question"
