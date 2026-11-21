"""Periodic cleanup for presence, sessions, tickets and relay peers."""

import asyncio
import logging
from contextlib import closing
from datetime import timedelta

from . import config, presence
from .database import get_db
from .multiplayer_relay import relay_registry
from .time_utils import now_iso, utcnow

log = logging.getLogger("gwsc.maintenance")


def _expire_sessions(conn) -> int:
    cutoff = (
        utcnow() - timedelta(seconds=config.SESSION_TIMEOUT_SECONDS)
    ).isoformat()
    now = now_iso()
    rows = conn.execute(
        """
        SELECT session_id FROM game_sessions
        WHERE is_active = 1 AND COALESCE(last_heartbeat, updated_at, created_at) <= ?
        """,
        (cutoff,),
    ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE game_sessions SET is_active = 0, state = 'closed', updated_at = ? "
            "WHERE session_id = ?",
            (now, row[0]),
        )
        conn.execute(
            "UPDATE session_members SET status = 'left', last_seen = ? WHERE session_id = ?",
            (now, row[0]),
        )
    return len(rows)


def _expire_tokens(conn) -> int:
    cursor = conn.execute("DELETE FROM tokens WHERE expires_at <= ?", (now_iso(),))
    return cursor.rowcount or 0


def _expire_tickets(conn) -> int:
    cursor = conn.execute(
        "DELETE FROM service_tickets WHERE expires_at <= ? AND issued_at <= ?",
        (now_iso(), (utcnow() - timedelta(days=1)).isoformat()),
    )
    return cursor.rowcount or 0


def run_cleanup_once() -> dict:
    """Run all cleanup tasks.  Returns counters for tests and metrics."""
    with closing(get_db()) as conn:
        with conn:
            result = {
                "presence": presence.expire_stale(conn),
                "sessions": _expire_sessions(conn),
                "tokens": _expire_tokens(conn),
                "tickets": _expire_tickets(conn),
                "relay_peers": relay_registry.expire(),
            }
    return result


async def maintenance_loop() -> None:
    log.info(
        "Maintenance worker started (interval=%ss)", config.MAINTENANCE_INTERVAL_SECONDS
    )
    try:
        while True:
            await asyncio.sleep(config.MAINTENANCE_INTERVAL_SECONDS)
            try:
                result = await asyncio.to_thread(run_cleanup_once)
                if any(result.values()):
                    log.info("Maintenance cleanup: %s", result)
            except Exception:
                log.exception("Maintenance cleanup failed")
    except asyncio.CancelledError:
        log.info("Maintenance worker stopped")
        raise
