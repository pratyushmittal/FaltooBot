from __future__ import annotations

from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_messages (
    chat_jid TEXT NOT NULL,
    message_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chat_jid, message_id)
);

CREATE TABLE IF NOT EXISTS chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_jid TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chat_history_chat_id_id
ON chat_history (chat_jid, id DESC);
"""


async def open_db(path: str) -> aiosqlite.Connection:
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA)
    await db.commit()
    return db


async def reserve_message(db: aiosqlite.Connection, chat_jid: str, message_id: str) -> bool:
    cursor = await db.execute(
        "INSERT OR IGNORE INTO processed_messages(chat_jid, message_id) VALUES(?, ?)",
        (chat_jid, message_id),
    )
    await db.commit()
    return cursor.rowcount == 1


async def add_turn(db: aiosqlite.Connection, chat_jid: str, role: str, content: str) -> None:
    await db.execute(
        "INSERT INTO chat_history(chat_jid, role, content) VALUES(?, ?, ?)",
        (chat_jid, role, content),
    )
    await db.commit()


async def recent_turns(db: aiosqlite.Connection, chat_jid: str, limit: int) -> list[dict[str, Any]]:
    cursor = await db.execute(
        """
        SELECT role, content
        FROM chat_history
        WHERE chat_jid = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (chat_jid, limit),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in reversed(rows)]


async def reset_chat(db: aiosqlite.Connection, chat_jid: str) -> None:
    await db.execute("DELETE FROM chat_history WHERE chat_jid = ?", (chat_jid,))
    await db.commit()
