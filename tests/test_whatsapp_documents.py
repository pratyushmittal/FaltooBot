from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from neonize.proto.waCommon.WACommon_pb2 import MessageKey
from neonize.proto.Neonize_pb2 import JID, MessageSource
from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message

from faltoobot.whatsapp import runtime


def _document_message() -> Message:
    message = Message()
    document = message.documentMessage
    document.fileName = "../Report Q1.pdf"
    document.mimetype = "application/pdf"
    document.fileLength = int(3.2 * 1024 * 1024)
    document.pageCount = 32
    return message


class FakeClient:
    async def download_any(self, message):
        return b"pdf bytes"


@pytest.mark.anyio
async def test_save_document_attachment_saves_in_workspace(tmp_path: Path) -> None:
    message = _document_message()
    note = await runtime.save_document_attachment(
        cast(Any, FakeClient()),
        message,
        document=message.documentMessage,
        workspace=tmp_path,
        message_id="abc/123",
    )

    assert note == "User has sent a file named Report-Q1.pdf of 3.2mb (32 pages)."
    assert (tmp_path / "Report-Q1.pdf").read_bytes() == b"pdf bytes"


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


@pytest.mark.anyio
async def test_get_turn_locked_adds_document_metadata_to_user_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from faltoobot import sessions
    from faltoobot.config import Config

    monkeypatch.setattr(sessions, "app_root", lambda: tmp_path / ".faltoobot")
    session = sessions.get_session(chat_key="15555555555555@lid")
    config = Config(
        home=tmp_path,
        root=tmp_path / ".faltoobot",
        config_file=tmp_path / ".faltoobot/config.toml",
        log_file=tmp_path / ".faltoobot/faltoobot.log",
        sessions_dir=tmp_path / ".faltoobot/sessions",
        session_db=tmp_path / ".faltoobot/session.db",
        launch_agent=tmp_path / "agent.plist",
        run_script=tmp_path / "run.sh",
        openai_api_key="",
        openai_oauth="",
        openai_model="gpt-5.5",
        openai_thinking="high",
        openai_fast=False,
        openai_transcription_model="gpt-4o-transcribe",
        allow_group_chats=set(),
        allowed_chats=set(),
        bot_name="Faltoo",
        browser_binary="",
    )
    message = Message()
    document = message.documentMessage
    document.fileName = "report.pdf"
    document.mimetype = "application/pdf"
    document.fileLength = int(3.2 * 1024 * 1024)
    document.pageCount = 32
    event = SimpleNamespace(
        Message=message,
        Info=SimpleNamespace(
            ID="doc-1",
            MessageSource=MessageSource(
                Chat=JID(User="15555555555555", Server="lid"),
                Sender=JID(User="15555555555555", Server="lid"),
                IsGroup=False,
            ),
            Message=MessageKey(ID="doc-1"),
        ),
    )

    turn = await runtime.get_turn_locked(
        cast(Any, FakeClient()),
        cast(Any, event),
        config=config,
        session=session,
        pending_albums={},
    )

    assert turn is not None
    assert turn["prompt"] == (
        "User has sent a file named report.pdf of 3.2mb (32 pages)."
    )
    assert (Path(sessions.get_messages(session)["workspace"]) / "report.pdf").exists()
    assert sessions.get_messages(session)["messages"] == []
