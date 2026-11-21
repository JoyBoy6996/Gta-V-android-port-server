import logging
from contextlib import closing
from typing import Any, Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import config
from ..auth import (
    get_current_session,
    get_current_user,
    revoke_other_user_tokens,
)
from ..database import get_db
from ..errors import api_error
from ..security import hash_password, verify_password
from ..time_utils import now_iso
from ..validators import validate_nickname, validate_password

log = logging.getLogger("gwsc.profile")
router = APIRouter(tags=["profile"])

PRESET_COLOURS = [
    "#1abc9c", "#2ecc71", "#3498db", "#9b59b6", "#34495e",
    "#f1c40f", "#e67e22", "#e74c3c", "#ecf0f1", "#95a5a6",
    "#d35400", "#c0392b", "#bdc3c7", "#7f8c8d", "#2c3e50",
]
VALID_AVATARS = {f"preset_{index}" for index in range(len(PRESET_COLOURS))} | set(
    PRESET_COLOURS
)


class UpdateNicknameRequest(BaseModel):
    nickname: str


class UpdatePasswordRequest(BaseModel):
    current: str
    next: str


class AvatarPresetRequest(BaseModel):
    preset: str


def _profile_payload(user: Dict[str, Any], friends_count: int) -> Dict[str, Any]:
    return {
        "rockstarId": user["rockstar_id"],
        "nickname": user["nickname"],
        "email": user["email"],
        "avatar": user["avatar"],
        "crewTag": user["crew_tag"] or "",
        "bio": user["bio"] or "",
        "status": user.get("status", "active"),
        "online": bool(user["is_online"]),
        "inGtao": bool(user["in_gtao"]),
        "friendsCount": friends_count,
        "createdAt": user["created_at"],
        "lastSeen": user["last_seen"],
    }


@router.get("/me")
@router.get("/gwsc/api/me")
async def get_my_profile(current_user: Dict[str, Any] = Depends(get_current_user)):
    with closing(get_db()) as conn:
        friends_count = conn.execute(
            """
            SELECT COUNT(*) FROM friends
            WHERE (user_id = ? OR friend_id = ?) AND status = 'accepted'
            """,
            (current_user["id"], current_user["id"]),
        ).fetchone()[0]
    return _profile_payload(current_user, friends_count)


@router.patch("/me")
@router.patch("/gwsc/api/me")
async def update_my_profile(
    req: UpdateNicknameRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    nickname = validate_nickname(req.nickname)
    if not nickname:
        raise api_error(
            400,
            "invalid_nickname",
            f"Nickname must be {config.NICKNAME_MIN_LENGTH}-{config.NICKNAME_MAX_LENGTH} "
            "characters and contain only letters, numbers, spaces, '.', '_' or '-'",
        )

    with closing(get_db()) as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE LOWER(nickname) = LOWER(?) AND id != ?",
            (nickname, current_user["id"]),
        ).fetchone()
        if existing:
            raise api_error(409, "nickname_taken", "Nickname is already in use")
        with conn:
            conn.execute(
                "UPDATE users SET nickname = ?, updated_at = ? WHERE id = ?",
                (nickname, now_iso(), current_user["id"]),
            )
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (current_user["id"],)
        ).fetchone()
        friends_count = conn.execute(
            """
            SELECT COUNT(*) FROM friends
            WHERE (user_id = ? OR friend_id = ?) AND status = 'accepted'
            """,
            (current_user["id"], current_user["id"]),
        ).fetchone()[0]
    return _profile_payload(dict(row), friends_count)


@router.post("/me/password")
@router.post("/gwsc/api/me/password")
async def update_password(
    req: UpdatePasswordRequest,
    session: Dict[str, Any] = Depends(get_current_session),
):
    user = session["user"]
    if not verify_password(req.current, user["password_hash"]):
        raise api_error(400, "invalid_password", "Current password is incorrect")
    password_error = validate_password(req.next)
    if password_error:
        raise api_error(400, "weak_password", password_error)
    if req.current == req.next:
        raise api_error(400, "password_unchanged", "New password must differ from the current one")

    with closing(get_db()) as conn:
        with conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (hash_password(req.next), now_iso(), user["id"]),
            )
    # Keep the current device signed in but revoke every other session.
    revoked = revoke_other_user_tokens(user["id"], session["token_hash"])
    log.info("Password changed for account %s; revoked %d other sessions", user["id"], revoked)
    return {"status": "ok"}


@router.get("/avatars/presets")
@router.get("/gwsc/api/avatars/presets")
async def get_avatar_presets():
    return {"colours": PRESET_COLOURS, "presets": sorted(VALID_AVATARS)}


@router.post("/me/avatar/preset")
@router.post("/gwsc/api/me/avatar/preset")
async def set_avatar_preset(
    req: AvatarPresetRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    if req.preset not in VALID_AVATARS:
        raise api_error(400, "invalid_avatar", "Unknown avatar preset")
    with closing(get_db()) as conn:
        with conn:
            conn.execute(
                "UPDATE users SET avatar = ?, updated_at = ? WHERE id = ?",
                (req.preset, now_iso(), current_user["id"]),
            )
    return {"status": "ok", "avatar": req.preset}


def _resolve_public_user(conn, identifier: str):
    try:
        numeric = int(identifier)
        row = conn.execute(
            "SELECT * FROM users WHERE rockstar_id = ?", (numeric,)
        ).fetchone()
    except ValueError:
        row = conn.execute(
            "SELECT * FROM users WHERE LOWER(nickname) = LOWER(?)", (identifier,)
        ).fetchone()
    return row


@router.get("/users/{user_id}/profile")
@router.get("/gwsc/api/users/{user_id}/profile")
async def get_user_profile(
    user_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    with closing(get_db()) as conn:
        target = _resolve_public_user(conn, user_id)
        if not target or target["status"] != "active":
            raise api_error(404, "user_not_found", "User not found")

        target_dict = dict(target)
        target_id = target_dict["id"]

        # If the target blocked the requester, do not reveal their profile.
        blocked = conn.execute(
            "SELECT 1 FROM friends WHERE user_id = ? AND friend_id = ? AND status = 'blocked'",
            (target_id, current_user["id"]),
        ).fetchone()
        if blocked:
            raise api_error(404, "user_not_found", "User not found")

        if target_id == current_user["id"]:
            relation = "self"
        else:
            rel_row = conn.execute(
                """
                SELECT user_id, status FROM friends
                WHERE (user_id = ? AND friend_id = ?) OR (user_id = ? AND friend_id = ?)
                """,
                (current_user["id"], target_id, target_id, current_user["id"]),
            ).fetchone()
            if not rel_row:
                relation = "none"
            elif rel_row["status"] == "blocked":
                relation = "blocked"
            elif rel_row["status"] == "accepted":
                relation = "friends"
            elif rel_row["status"] == "pending":
                relation = (
                    "outgoing"
                    if rel_row["user_id"] == current_user["id"]
                    else "incoming"
                )
            else:
                relation = "none"

        friends_count = conn.execute(
            """
            SELECT COUNT(*) FROM friends
            WHERE (user_id = ? OR friend_id = ?) AND status = 'accepted'
            """,
            (target_id, target_id),
        ).fetchone()[0]
        ach_count = conn.execute(
            "SELECT COUNT(*) FROM achievements WHERE user_id = ?", (target_id,)
        ).fetchone()[0]
        recent = conn.execute(
            """
            SELECT achievement_id, unlocked_at FROM achievements
            WHERE user_id = ? ORDER BY unlocked_at DESC LIMIT 5
            """,
            (target_id,),
        ).fetchall()

    return {
        "rockstarId": target_dict["rockstar_id"],
        "nickname": target_dict["nickname"],
        "relation": relation,
        "online": bool(target_dict["is_online"]),
        "inGtao": bool(target_dict["in_gtao"]),
        "avatar": target_dict["avatar"],
        "crewTag": target_dict["crew_tag"] or "",
        "lastSeen": target_dict["last_seen"],
        "createdAt": target_dict["created_at"],
        "bio": target_dict["bio"] or "",
        "friendsCount": friends_count,
        "achievements": {
            "count": ach_count,
            "total": 77,
            "recent": [
                {"id": row["achievement_id"], "at": row["unlocked_at"]}
                for row in recent
            ],
        },
        "game": {
            "hasStats": True,
            "characters": [
                {
                    "active": True,
                    "rank": target_dict["rank"],
                    "wallet": target_dict["wallet"],
                    "bank": target_dict["bank"],
                }
            ],
        },
    }


def _resolve_target(conn, identifier: int):
    row = conn.execute(
        "SELECT id, rockstar_id, nickname FROM users WHERE rockstar_id = ?", (identifier,)
    ).fetchone()
    return row


@router.post("/users/{user_id}/block")
@router.post("/gwsc/api/users/{user_id}/block")
async def block_user(
    user_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    with closing(get_db()) as conn:
        target = _resolve_target(conn, user_id)
        if not target:
            raise api_error(404, "user_not_found", "User not found")
        if target["id"] == current_user["id"]:
            raise api_error(400, "self_block", "You cannot block yourself")
        now = now_iso()
        with conn:
            conn.execute(
                """
                INSERT INTO friends (user_id, friend_id, status, created_at, updated_at)
                VALUES (?, ?, 'blocked', ?, ?)
                ON CONFLICT(user_id, friend_id) DO UPDATE SET
                    status = 'blocked', updated_at = excluded.updated_at
                """,
                (current_user["id"], target["id"], now, now),
            )
    return {"status": "ok"}


@router.delete("/users/{user_id}/block")
@router.delete("/gwsc/api/users/{user_id}/block")
async def unblock_user(
    user_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    with closing(get_db()) as conn:
        target = _resolve_target(conn, user_id)
        if not target:
            raise api_error(404, "user_not_found", "User not found")
        with conn:
            conn.execute(
                "DELETE FROM friends WHERE user_id = ? AND friend_id = ? AND status = 'blocked'",
                (current_user["id"], target["id"]),
            )
    return {"status": "ok"}
