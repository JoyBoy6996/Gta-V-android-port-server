import datetime
import logging
from typing import Optional, Dict, Any, List
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Depends, status
from ..database import get_db
from ..auth import get_current_user, hash_password, verify_password

log = logging.getLogger("gwsc.profile")
router = APIRouter(tags=["profile"])

PRESET_COLOURS = [
    "#1abc9c", "#2ecc71", "#3498db", "#9b59b6", "#34495e",
    "#f1c40f", "#e67e22", "#e74c3c", "#ecf0f1", "#95a5a6",
    "#d35400", "#c0392b", "#bdc3c7", "#7f8c8d", "#2c3e50"
]

class UpdateNicknameRequest(BaseModel):
    nickname: str

class UpdatePasswordRequest(BaseModel):
    current: str
    next: str

class AvatarPresetRequest(BaseModel):
    preset: str

@router.get("/me")
@router.get("/gwsc/api/me")
async def get_my_profile(current_user: Dict[str, Any] = Depends(get_current_user)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*) FROM friends
        WHERE (user_id = ? OR friend_id = ?) AND status = 'accepted'
    """, (current_user["id"], current_user["id"]))
    friends_count = cursor.fetchone()[0]
    conn.close()

    return {
        "rockstarId": current_user["rockstar_id"],
        "nickname": current_user["nickname"],
        "email": current_user["email"],
        "avatar": current_user["avatar"],
        "crewTag": current_user["crew_tag"],
        "bio": current_user["bio"],
        "online": bool(current_user["is_online"]),
        "inGtao": bool(current_user["in_gtao"]),
        "friendsCount": friends_count,
        "createdAt": current_user["created_at"],
        "lastSeen": current_user["last_seen"]
    }

@router.patch("/me")
@router.patch("/gwsc/api/me")
async def update_my_profile(req: UpdateNicknameRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    new_nick = req.nickname.strip()
    if len(new_nick) < 3 or len(new_nick) > 30:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_nickname", "message": "Nickname must be between 3 and 30 characters"}
        )

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE LOWER(nickname) = LOWER(?) AND id != ?", (new_nick, current_user["id"]))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "nickname_taken", "message": "Nickname is already in use"}
        )

    with conn:
        conn.execute("UPDATE users SET nickname = ? WHERE id = ?", (new_nick, current_user["id"]))
    conn.close()

    current_user["nickname"] = new_nick
    return {
        "rockstarId": current_user["rockstar_id"],
        "nickname": new_nick,
        "email": current_user["email"],
        "avatar": current_user["avatar"],
        "crewTag": current_user["crew_tag"]
    }

@router.post("/me/password")
@router.post("/gwsc/api/me/password")
async def update_password(req: UpdatePasswordRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    if not verify_password(req.current, current_user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_password", "message": "Current password is incorrect"}
        )
    if len(req.next) < 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "weak_password", "message": "Next password must be at least 4 characters"}
        )

    new_hash = hash_password(req.next)
    conn = get_db()
    with conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, current_user["id"]))
    conn.close()
    return {"status": "ok"}

@router.get("/avatars/presets")
@router.get("/gwsc/api/avatars/presets")
async def get_avatar_presets():
    return {
        "colours": PRESET_COLOURS
    }

@router.post("/me/avatar/preset")
@router.post("/gwsc/api/me/avatar/preset")
async def set_avatar_preset(req: AvatarPresetRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    conn = get_db()
    with conn:
        conn.execute("UPDATE users SET avatar = ? WHERE id = ?", (req.preset, current_user["id"]))
    conn.close()
    return {"status": "ok", "avatar": req.preset}

@router.get("/users/{user_id}/profile")
@router.get("/gwsc/api/users/{user_id}/profile")
async def get_user_profile(user_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    conn = get_db()
    cursor = conn.cursor()
    # Support lookup by rockstar_id or db id
    try:
        rid = int(user_id)
        cursor.execute("SELECT * FROM users WHERE rockstar_id = ? OR id = ?", (rid, rid))
    except ValueError:
        cursor.execute("SELECT * FROM users WHERE LOWER(nickname) = LOWER(?)", (user_id,))

    target = cursor.fetchone()
    if not target:
        conn.close()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "user_not_found", "message": "User not found"}
        )

    target_dict = dict(target)
    target_id = target_dict["id"]

    # Determine relation to current user
    if target_id == current_user["id"]:
        relation = "self"
    else:
        cursor.execute("""
            SELECT status, user_id FROM friends
            WHERE (user_id = ? AND friend_id = ?) OR (user_id = ? AND friend_id = ?)
        """, (current_user["id"], target_id, target_id, current_user["id"]))
        rel_row = cursor.fetchone()
        if not rel_row:
            relation = "none"
        elif rel_row["status"] == "blocked":
            relation = "blocked"
        elif rel_row["status"] == "accepted":
            relation = "friends"
        elif rel_row["status"] == "pending_outgoing":
            if rel_row["user_id"] == current_user["id"]:
                relation = "outgoing"
            else:
                relation = "incoming"
        else:
            relation = "none"

    cursor.execute("""
        SELECT COUNT(*) FROM friends
        WHERE (user_id = ? OR friend_id = ?) AND status = 'accepted'
    """, (target_id, target_id))
    friends_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM achievements WHERE user_id = ?", (target_id,))
    ach_count = cursor.fetchone()[0]

    cursor.execute("""
        SELECT achievement_id, unlocked_at FROM achievements
        WHERE user_id = ? ORDER BY unlocked_at DESC LIMIT 5
    """, (target_id,))
    recent_achs = [{"id": r["achievement_id"], "at": r["unlocked_at"]} for r in cursor.fetchall()]
    conn.close()

    return {
        "rockstarId": target_dict["rockstar_id"],
        "nickname": target_dict["nickname"],
        "relation": relation,
        "online": bool(target_dict["is_online"]),
        "inGtao": bool(target_dict["in_gtao"]),
        "avatar": target_dict["avatar"],
        "crewTag": target_dict["crew_tag"],
        "lastSeen": target_dict["last_seen"],
        "createdAt": target_dict["created_at"],
        "bio": target_dict["bio"],
        "friendsCount": friends_count,
        "achievements": {
            "count": ach_count,
            "total": 77,
            "recent": recent_achs
        },
        "game": {
            "hasStats": True,
            "characters": [
                {
                    "active": True,
                    "rank": target_dict["rank"],
                    "wallet": target_dict["wallet"],
                    "bank": target_dict["bank"]
                }
            ]
        }
    }

@router.post("/users/{user_id}/block")
@router.post("/gwsc/api/users/{user_id}/block")
async def block_user(user_id: int, current_user: Dict[str, Any] = Depends(get_current_user)):
    conn = get_db()
    with conn:
        conn.execute("""
            INSERT INTO friends (user_id, friend_id, status, created_at)
            VALUES (?, ?, 'blocked', ?)
            ON CONFLICT(user_id, friend_id) DO UPDATE SET status = 'blocked'
        """, (current_user["id"], user_id, datetime.datetime.now(datetime.timezone.utc).isoformat()))
    conn.close()
    return {"status": "ok"}

@router.delete("/users/{user_id}/block")
@router.delete("/gwsc/api/users/{user_id}/block")
async def unblock_user(user_id: int, current_user: Dict[str, Any] = Depends(get_current_user)):
    conn = get_db()
    with conn:
        conn.execute("""
            DELETE FROM friends
            WHERE user_id = ? AND friend_id = ? AND status = 'blocked'
        """, (current_user["id"], user_id))
    conn.close()
    return {"status": "ok"}
