"""Authentication primitives and FastAPI dependencies.

A bearer token is an opaque random value.  Only its SHA-256 hash is stored.
Every authenticated request checks expiry, revocation (row presence) and
account status before returning the account.
"""

import logging
import sqlite3
import threading
import time
from typing import Any, Dict, Optional

from fastapi import Depends, Header

from . import config, presence
from .database import get_db
from .errors import api_error
from .security import generate_token, hash_token
from .time_utils import iso_after, is_expired, now_iso, parse_iso

log = logging.getLogger("gwsc.auth")

_last_touch: Dict[int, float] = {}
_touch_lock = threading.Lock()


def extract_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1]


def create_user_token(user_id: int, device: str = "") -> str:
    raw = generate_token()
    token_hash = hash_token(raw)
    now = now_iso()
    expires_at = iso_after(config.TOKEN_EXPIRY_SECONDS)
    conn = get_db()
    try:
        with conn:
            # Drop expired rows for this account lazily.
            conn.execute(
                "DELETE FROM tokens WHERE user_id = ? AND expires_at <= ?",
                (user_id, now),
            )
            conn.execute(
                "INSERT INTO tokens (token_hash, user_id, created_at, expires_at, device) "
                "VALUES (?, ?, ?, ?, ?)",
                (token_hash, user_id, now, expires_at, device[:200]),
            )
            # Bound concurrent sessions per account.
            rows = conn.execute(
                "SELECT token_hash FROM tokens WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
            for row in rows[20:]:
                conn.execute("DELETE FROM tokens WHERE token_hash = ?", (row[0],))
    finally:
        conn.close()
    return raw


def get_token_record(raw_token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw_token:
        return None
    token_hash = hash_token(raw_token)
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT t.token_hash, t.user_id AS token_user_id, t.expires_at,
                   t.last_used_at, u.*
            FROM tokens t
            JOIN users u ON u.id = t.user_id
            WHERE t.token_hash = ?
            """,
            (token_hash,),
        ).fetchone()
        if not row:
            return None
        record = dict(row)
        if is_expired(record.get("expires_at")):
            with conn:
                conn.execute("DELETE FROM tokens WHERE token_hash = ?", (token_hash,))
            return None
        if record.get("status") != "active":
            return None
        # Update last_used_at at most once per interval.
        last_used = parse_iso(record.get("last_used_at"))
        if last_used is None or (parse_iso(now_iso()) - last_used).total_seconds() >= config.TOKEN_LAST_USED_UPDATE_SECONDS:
            with conn:
                conn.execute(
                    "UPDATE tokens SET last_used_at = ? WHERE token_hash = ?",
                    (now_iso(), token_hash),
                )
        return {
            "token_hash": token_hash,
            "user_id": record["id"],
            "expires_at": record["expires_at"],
            "user": record,
        }
    finally:
        conn.close()


def get_user_by_token(raw_token: Optional[str]) -> Optional[Dict[str, Any]]:
    record = get_token_record(raw_token)
    if not record:
        return None
    user = dict(record["user"])
    user.pop("password_hash", None)
    return user


def touch_activity(user_id: int, state: str = "launcher") -> None:
    """Refresh presence, preserving an existing in-game/session state."""
    now = time.monotonic()
    with _touch_lock:
        last = _last_touch.get(user_id, 0.0)
        if now - last < config.PRESENCE_TOUCH_INTERVAL_SECONDS:
            return
        _last_touch[user_id] = now
    conn = get_db()
    try:
        with conn:
            existing = conn.execute(
                "SELECT state, session_id FROM presence_records WHERE account_id = ?",
                (user_id,),
            ).fetchone()
            if existing:
                presence.touch(
                    conn,
                    user_id,
                    state=existing["state"],
                    session_id=existing["session_id"],
                )
            else:
                presence.touch(conn, user_id, state=state)
    except sqlite3.DatabaseError:
        log.warning("Could not update presence for account %s", user_id, exc_info=True)
    finally:
        conn.close()


def revoke_token(raw_token: Optional[str]) -> None:
    if not raw_token:
        return
    token_hash = hash_token(raw_token)
    conn = get_db()
    try:
        with conn:
            conn.execute("DELETE FROM tokens WHERE token_hash = ?", (token_hash,))
    finally:
        conn.close()


def revoke_all_user_tokens(user_id: int) -> None:
    conn = get_db()
    try:
        with conn:
            conn.execute("DELETE FROM tokens WHERE user_id = ?", (user_id,))
    finally:
        conn.close()


def revoke_other_user_tokens(user_id: int, keep_token_hash: Optional[str]) -> int:
    conn = get_db()
    try:
        with conn:
            if keep_token_hash:
                cursor = conn.execute(
                    "DELETE FROM tokens WHERE user_id = ? AND token_hash != ?",
                    (user_id, keep_token_hash),
                )
            else:
                cursor = conn.execute(
                    "DELETE FROM tokens WHERE user_id = ?", (user_id,)
                )
            return cursor.rowcount or 0
    finally:
        conn.close()


def count_active_tokens(user_id: int) -> int:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM tokens WHERE user_id = ? AND expires_at > ?",
            (user_id, now_iso()),
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


async def get_current_session(
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    raw_token = extract_bearer(authorization)
    if not raw_token:
        raise api_error(
            401,
            "unauthorized",
            "Missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    record = get_token_record(raw_token)
    if not record:
        raise api_error(
            401,
            "unauthorized",
            "Invalid, expired or revoked token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    touch_activity(record["user_id"], state="launcher")
    record["raw_token"] = raw_token
    return record


async def get_current_user(
    session: Dict[str, Any] = Depends(get_current_session),
) -> Dict[str, Any]:
    user = dict(session["user"])
    user.pop("password_hash", None)
    return user


async def require_active_account(
    user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    if user.get("status") != "active":
        raise api_error(403, "account_disabled", "This account is not active")
    return user


def require_resource_owner(resource_user_id: int, current_user: Dict[str, Any]) -> None:
    if int(resource_user_id) != int(current_user["id"]):
        raise api_error(403, "forbidden", "You do not own this resource")


def authenticate_native_request(
    *,
    form_token: Optional[str] = None,
    query_token: Optional[str] = None,
    authorization: Optional[str] = None,
    header_token: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve a native ROS caller from any observed credential location."""
    raw = form_token or query_token or header_token or extract_bearer(authorization)
    return get_user_by_token(raw)
