"""
sessions.py — Lightweight session metadata store backed by SQLite.

Each "session" maps to a LangGraph thread_id.  This module only stores
the metadata (id, title, creation time); the actual conversation state
is managed by the LangGraph SqliteSaver checkpointer in agent.py.
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

_DB_PATH = str(DATA_DIR / "sessions.db")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id         TEXT PRIMARY KEY,
            title      TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


_conn = _get_conn()


def create_session(title: Optional[str] = None) -> dict:
    """Create a new session and return its metadata dict."""
    session_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    if not title:
        title = f"Chat {datetime.now(timezone.utc).strftime('%b %d, %H:%M')}"
    _conn.execute(
        "INSERT INTO sessions (id, title, created_at) VALUES (?, ?, ?)",
        (session_id, title, now),
    )
    _conn.commit()
    return {"id": session_id, "title": title, "created_at": now}


def list_sessions() -> list[dict]:
    """Return all sessions ordered by most recent first."""
    rows = _conn.execute(
        "SELECT id, title, created_at FROM sessions ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_session(session_id: str) -> Optional[dict]:
    """Return a single session's metadata, or None if not found."""
    row = _conn.execute(
        "SELECT id, title, created_at FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    return dict(row) if row else None


def update_title(session_id: str, title: str) -> bool:
    """Update a session's title. Returns True if the session existed."""
    cur = _conn.execute(
        "UPDATE sessions SET title = ? WHERE id = ?",
        (title, session_id),
    )
    _conn.commit()
    return cur.rowcount > 0


def delete_session(session_id: str) -> bool:
    """Delete a session. Returns True if it existed."""
    cur = _conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    _conn.commit()
    return cur.rowcount > 0
