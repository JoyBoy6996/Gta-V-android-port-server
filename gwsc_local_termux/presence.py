"""Presence lifecycle.

Presence is derived from an explicit heartbeat record with an expiry.  The
legacy prototype used a boolean ``is_online`` flag that never expired, which
made ``/info`` report users as online forever.
"""

import json
import sqlite3
from typing import Any, Dict, Optional

from . import config
from .time_utils import iso_after, now_iso, utcnow

VALID_STATES = {"offline", "launcher", "in_game", "in_session", "away"}


def touch(
    conn: sqlite3.Connection,
    user_id: int,
    state: str = "launcher",
    session_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    if state not in VALID_STATES:
        state = "launcher"
    if state == "offline":
        mark_offline(conn, user_id)
        return
    now = now_iso()
    expires = iso_after(config.PRESENCE_TIMEOUT_SECONDS)
    payload = json.dumps(metadata or {}, separators=(",", ":"))
    conn.execute(
        """
        INSERT INTO presence_records
            (account_id, state, session_id, last_heartbeat, expires_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id) DO UPDATE SET
            state = excluded.state,
            session_id = excluded.session_id,
            last_heartbeat = excluded.last_heartbeat,
            expires_at = excluded.expires_at,
            metadata = excluded.metadata
        """,
        (user_id, state, session_id, now, expires, payload),
    )
    conn.execute(
        """
        UPDATE users
        SET is_online = 1,
            in_gtao = CASE WHEN ? IN ('in_game', 'in_session') THEN 1 ELSE 0 END,
            last_activity = ?,
            last_seen = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (state, now, now, now, user_id),
    )


def mark_offline(conn: sqlite3.Connection, user_id: int) -> None:
    now = now_iso()
    conn.execute(
        "DELETE FROM presence_records WHERE account_id = ?", (user_id,)
    )
    conn.execute(
        """
        UPDATE users
        SET is_online = 0, in_gtao = 0, last_seen = ?, updated_at = ?
        WHERE id = ?
        """,
        (now, now, user_id),
    )


def online_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) FROM presence_records p
        JOIN users u ON u.id = p.account_id
        WHERE p.expires_at > ? AND u.status = 'active' AND p.state != 'offline'
        """,
        (now_iso(),),
    ).fetchone()
    return int(row[0]) if row else 0


def get_presence(conn: sqlite3.Connection, user_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM presence_records WHERE account_id = ?", (user_id,)
    ).fetchone()
    if not row:
        return None
    record = dict(row)
    if record.get("state") == "offline":
        return None
    if record.get("expires_at", "") <= now_iso():
        return None
    return record


def expire_stale(conn: sqlite3.Connection) -> int:
    now = now_iso()
    stale = conn.execute(
        "SELECT account_id FROM presence_records WHERE expires_at <= ?", (now,)
    ).fetchall()
    if not stale:
        return 0
    ids = [row[0] for row in stale]
    placeholders = ",".join("?" for _ in ids)
    conn.execute(f"DELETE FROM presence_records WHERE account_id IN ({placeholders})", ids)
    conn.execute(
        f"UPDATE users SET is_online = 0, in_gtao = 0, updated_at = ? "
        f"WHERE id IN ({placeholders})",
        [now, *ids],
    )
    return len(ids)


def is_online(conn: sqlite3.Connection, user_id: int) -> bool:
    return get_presence(conn, user_id) is not None


def stale_cutoff_iso() -> str:
    """ISO timestamp before which a user is considered offline."""
    from datetime import timedelta

    return (utcnow() - timedelta(seconds=config.PRESENCE_TIMEOUT_SECONDS)).isoformat()
