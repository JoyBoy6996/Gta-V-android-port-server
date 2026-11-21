"""Private service tickets and their account resolution.

Two ticket formats coexist:

* legacy ROS XML tickets (``gwsc_ticket_...``) issued by ``CreateTicket``;
* launcher launch tickets: exactly 43 URL-safe characters, required by the
  installed Android client's ``/gwsc/api/game-launch`` response contract
  (``ticket.matches("[A-Za-z0-9_-]{43}")``).

Only a SHA-256 hash is stored.  A ticket is bound to an account and a service,
has an expiry and can be revoked.  Tickets are never logged or returned by
anything other than the endpoint that minted them.
"""

import logging
import re
import secrets
import threading
import time
from typing import Any, Dict, Optional

from . import config
from .database import get_db
from .security import hash_token
from .time_utils import iso_after, is_expired, now_iso

log = logging.getLogger("gwsc.tickets")

LAUNCH_TICKET_LENGTH = 43
LAUNCH_TICKET_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
GAME_LAUNCH_SERVICE = "game-launch"

_last_used_touch: Dict[int, float] = {}
_last_used_lock = threading.Lock()


def generate_launch_ticket() -> str:
    """Return a 43-character URL-safe ticket for the launcher contract."""
    ticket = secrets.token_urlsafe(32)
    # token_urlsafe(32) is 43 characters today.  Keep the guard explicit so a
    # future change cannot silently break the client-side regex.
    if len(ticket) != LAUNCH_TICKET_LENGTH or not LAUNCH_TICKET_RE.match(ticket):
        raise RuntimeError("launch ticket generator produced an invalid value")
    return ticket


def mint_service_ticket(
    conn,
    account_id: int,
    *,
    service: str,
    raw_ticket: str,
    title: str = "",
    platform: str = "android",
    expiry_seconds: int = config.TICKET_EXPIRY_SECONDS,
    max_per_service: Optional[int] = None,
) -> str:
    """Persist the hash of ``raw_ticket`` and return the raw value.

    Expired tickets for the account are pruned.  When ``max_per_service`` is
    set, only that many tickets for the same service are retained, so a burst
    of launches cannot evict tickets belonging to another service.
    """
    now = now_iso()
    expires_at = iso_after(expiry_seconds)
    with conn:
        conn.execute(
            "DELETE FROM service_tickets WHERE account_id = ? AND expires_at <= ?",
            (account_id, now),
        )
        if max_per_service is not None and max_per_service > 0:
            rows = conn.execute(
                "SELECT id FROM service_tickets "
                "WHERE account_id = ? AND service = ? "
                "ORDER BY issued_at DESC",
                (account_id, service),
            ).fetchall()
            for row in rows[max(0, max_per_service - 1):]:
                conn.execute("DELETE FROM service_tickets WHERE id = ?", (row["id"],))
        conn.execute(
            """
            INSERT INTO service_tickets
                (ticket_hash, account_id, service, title, platform,
                 issued_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hash_token(raw_ticket),
                account_id,
                service,
                title[:80],
                platform[:40],
                now,
                expires_at,
            ),
        )
    return raw_ticket


def _touch_last_used(conn, ticket_id: int) -> None:
    now = time.monotonic()
    with _last_used_lock:
        last = _last_used_touch.get(ticket_id, 0.0)
        if now - last < config.TOKEN_LAST_USED_UPDATE_SECONDS:
            return
        _last_used_touch[ticket_id] = now
    with conn:
        conn.execute(
            "UPDATE service_tickets SET last_used_at = ? WHERE id = ?",
            (now_iso(), ticket_id),
        )


def resolve_service_ticket(raw_ticket: Optional[str]) -> Optional[Dict[str, Any]]:
    """Resolve a native caller from a service ticket.

    Returns the same account shape as :func:`gwsc_local_termux.auth.get_user_by_token`,
    or ``None`` when the ticket is unknown, revoked, expired or belongs to a
    non-active account.
    """
    if not raw_ticket or len(raw_ticket) > 128:
        return None
    ticket_hash = hash_token(raw_ticket)
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT t.id AS ticket_id,
                   t.expires_at AS ticket_expires_at,
                   t.revoked_at AS ticket_revoked_at,
                   u.*
            FROM service_tickets t
            JOIN users u ON u.id = t.account_id
            WHERE t.ticket_hash = ?
            """,
            (ticket_hash,),
        ).fetchone()
        if not row:
            return None
        if row["ticket_revoked_at"]:
            return None
        if is_expired(row["ticket_expires_at"]):
            with conn:
                conn.execute(
                    "DELETE FROM service_tickets WHERE id = ?", (row["ticket_id"],)
                )
            return None
        if row["status"] != "active":
            return None
        _touch_last_used(conn, row["ticket_id"])
        user = {
            key: value
            for key, value in dict(row).items()
            if not key.startswith("ticket_")
        }
        user.pop("password_hash", None)
        return user
    finally:
        conn.close()


def revoke_service_ticket(raw_ticket: Optional[str], account_id: int) -> bool:
    """Revoke a ticket owned by ``account_id``.  Returns whether a row changed."""
    if not raw_ticket:
        return False
    conn = get_db()
    try:
        with conn:
            cursor = conn.execute(
                "UPDATE service_tickets SET revoked_at = ? "
                "WHERE ticket_hash = ? AND account_id = ? AND revoked_at IS NULL",
                (now_iso(), hash_token(raw_ticket), account_id),
            )
        return bool(cursor.rowcount)
    finally:
        conn.close()
