"""
gateway/bindings.py — channel ↔ session binding store.

This module keeps track of which backend session_id a given
channel + channel_user_id pair is currently bound to.

Example keys:
  - channel="telegram", channel_user_id="123456789"
  - channel="whatsapp", channel_user_id="+19991234567"

The values are backend session identifiers (thread_ids) that
your LangChain agent uses for short-term memory.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "bindings.db"


def _get_conn() -> sqlite3.Connection:
    """
    Create (or reuse) a SQLite connection and ensure the table exists.

    We keep a single module-level connection so that repeated calls
    are cheap and thread-safe enough for this small demo.
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS channel_bindings (
            channel TEXT NOT NULL,
            channel_user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (channel, channel_user_id)
        )
        """
    )
    return conn


_CONN = _get_conn()


def get_binding(channel: str, channel_user_id: str) -> Optional[str]:
    """
    Look up the current session_id for (channel, channel_user_id).

    Returns:
        session_id string if it exists, otherwise None.
    """
    cur = _CONN.execute(
        "SELECT session_id FROM channel_bindings WHERE channel = ? AND channel_user_id = ?",
        (channel, channel_user_id),
    )
    row = cur.fetchone()
    return row[0] if row else None


def set_binding(channel: str, channel_user_id: str, session_id: str) -> None:
    """
    Upsert a binding so that this (channel, channel_user_id) now points to session_id.
    """
    now = datetime.now(timezone.utc).isoformat()
    _CONN.execute(
        """
        INSERT INTO channel_bindings (channel, channel_user_id, session_id, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(channel, channel_user_id)
        DO UPDATE SET session_id = excluded.session_id,
                      updated_at = excluded.updated_at
        """,
        (channel, channel_user_id, session_id, now),
    )
    _CONN.commit()


def clear_binding(channel: str, channel_user_id: str) -> None:
    """
    Remove the binding for this (channel, channel_user_id) if it exists.
    """
    _CONN.execute(
        "DELETE FROM channel_bindings WHERE channel = ? AND channel_user_id = ?",
        (channel, channel_user_id),
    )
    _CONN.commit()

