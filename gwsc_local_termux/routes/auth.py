import logging
import sqlite3
from contextlib import closing
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel

from .. import config, presence
from ..auth import (
    count_active_tokens,
    create_user_token,
    extract_bearer,
    get_current_user,
    get_token_record,
    revoke_all_user_tokens,
    revoke_token,
    touch_activity,
)
from ..database import get_db, get_next_rockstar_id
from ..errors import api_error
from ..multiplayer_relay import relay_registry
from ..request_utils import rate_key
from ..security import (
    hash_password,
    password_needs_rehash,
    rate_limiter,
    verify_password,
)
from ..time_utils import now_iso
from ..validators import (
    normalise_email,
    validate_email,
    validate_nickname,
    validate_password,
)

log = logging.getLogger("gwsc.auth")
router = APIRouter(tags=["auth"])


class RegisterRequest(BaseModel):
    email: str
    nickname: str
    password: str
    client: Optional[str] = "launcher"


class LoginRequest(BaseModel):
    email: str
    password: str
    client: Optional[str] = "launcher"


def build_profile(user: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "rockstarId": user["rockstar_id"],
        "nickname": user["nickname"],
        "email": user["email"],
        "avatar": user["avatar"],
        "crewTag": user["crew_tag"] or "",
        "online": bool(user["is_online"]),
    }


def _enforce_rate_limit(key: str, limit: int, window: int) -> None:
    allowed, retry_after = rate_limiter.check(key, limit, window)
    if not allowed:
        raise api_error(
            429,
            "rate_limited",
            "Too many requests. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )


@router.post("/register")
@router.post("/gwsc/api/register")
async def register(req: RegisterRequest, request: Request):
    if not config.REGISTRATION_ENABLED:
        raise api_error(403, "registration_disabled", "User registration is currently closed")

    _enforce_rate_limit(
        rate_key(request, "register"),
        config.REGISTER_RATE_LIMIT,
        config.REGISTER_RATE_WINDOW,
    )

    email = validate_email(req.email)
    nickname = validate_nickname(req.nickname)
    password_error = validate_password(req.password)
    if not email:
        raise api_error(400, "invalid_email", "A valid email address is required")
    if not nickname:
        raise api_error(
            400,
            "invalid_nickname",
            f"Nickname must be {config.NICKNAME_MIN_LENGTH}-{config.NICKNAME_MAX_LENGTH} "
            "characters and contain only letters, numbers, spaces, '.', '_' or '-'",
        )
    if password_error:
        raise api_error(400, "invalid_password", password_error)

    now = now_iso()
    password_hash = hash_password(req.password)

    with closing(get_db()) as conn:
        try:
            with conn:
                existing = conn.execute(
                    "SELECT id FROM users WHERE email = ?", (email,)
                ).fetchone()
                if existing:
                    raise api_error(
                        409, "email_exists", "An account with this email already exists"
                    )
                existing = conn.execute(
                    "SELECT id FROM users WHERE LOWER(nickname) = LOWER(?)", (nickname,)
                ).fetchone()
                if existing:
                    raise api_error(409, "nickname_taken", "This nickname is already in use")

                rockstar_id = get_next_rockstar_id(conn)
                cursor = conn.execute(
                    """
                    INSERT INTO users
                        (rockstar_id, email, nickname, password_hash, avatar,
                         created_at, updated_at, last_seen, last_activity)
                    VALUES (?, ?, ?, ?, 'preset_0', ?, ?, ?, ?)
                    """,
                    (rockstar_id, email, nickname, password_hash, now, now, now, now),
                )
                user_id = cursor.lastrowid
                presence.touch(conn, user_id, state="launcher")
        except sqlite3.IntegrityError:
            raise api_error(409, "account_exists", "Account or nickname already exists")

    token = create_user_token(user_id, device=req.client or "")
    log.info("Registered account %s (public id %d)", nickname, rockstar_id)

    with closing(get_db()) as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        user = dict(row)
    return {"token": token, "profile": build_profile(user)}


@router.post("/login")
@router.post("/gwsc/api/login")
async def login(req: LoginRequest, request: Request):
    email = normalise_email(req.email)
    _enforce_rate_limit(
        rate_key(request, "login:ip"), config.LOGIN_RATE_LIMIT, config.LOGIN_RATE_WINDOW
    )
    _enforce_rate_limit(
        rate_key(request, "login:account", email),
        config.LOGIN_RATE_LIMIT,
        config.LOGIN_RATE_WINDOW,
    )

    with closing(get_db()) as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ? OR LOWER(nickname) = ?",
            (email, email),
        ).fetchone()
        if not row:
            raise api_error(401, "invalid_credentials", "Invalid email or password")
        user = dict(row)
        if not verify_password(req.password, user["password_hash"]):
            raise api_error(401, "invalid_credentials", "Invalid email or password")
        if user.get("status") != "active":
            raise api_error(403, "account_disabled", "This account is not active")

        now = now_iso()
        with conn:
            # Transparently upgrade legacy or weaker password hashes.
            if password_needs_rehash(user["password_hash"]):
                conn.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (hash_password(req.password), user["id"]),
                )
            conn.execute(
                "UPDATE users SET last_login_at = ?, last_seen = ?, updated_at = ? WHERE id = ?",
                (now, now, now, user["id"]),
            )
            presence.touch(conn, user["id"], state="launcher")

    # Re-read so the returned profile reflects last_login_at and presence.
    with closing(get_db()) as conn:
        user = dict(
            conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        )

    token = create_user_token(user["id"], device=req.client or "")
    touch_activity(user["id"], state="launcher")
    log.info("Account logged in: %s (public id %d)", user["nickname"], user["rockstar_id"])
    return {"token": token, "profile": build_profile(user)}


@router.post("/logout")
@router.post("/gwsc/api/logout")
async def logout(authorization: Optional[str] = Header(None)):
    raw_token = extract_bearer(authorization)
    if raw_token:
        record = get_token_record(raw_token)
        revoke_token(raw_token)
        if record:
            user_id = record["user_id"]
            relay_registry.unregister_account(user_id)
            if count_active_tokens(user_id) == 0:
                with closing(get_db()) as conn:
                    with conn:
                        presence.mark_offline(conn, user_id)
    return {"status": "ok"}


@router.post("/logout-all")
@router.post("/gwsc/api/logout-all")
async def logout_all(current_user: Dict[str, Any] = Depends(get_current_user)):
    with closing(get_db()) as conn:
        with conn:
            presence.mark_offline(conn, current_user["id"])
    revoke_all_user_tokens(current_user["id"])
    relay_registry.unregister_account(current_user["id"])
    return {"status": "ok"}
