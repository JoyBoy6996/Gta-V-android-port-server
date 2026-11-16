import datetime
import logging
from typing import Optional, Dict, Any, List
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Depends, status
from ..database import get_db
from ..auth import get_current_user
from .events import event_bus

log = logging.getLogger("gwsc.friends")
router = APIRouter(tags=["friends"])

class AddFriendRequest(BaseModel):
    nickname: str

def format_friend_item(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "rockstarId": row["rockstar_id"],
        "nickname": row["nickname"],
        "online": bool(row["is_online"]),
        "inGtao": bool(row["in_gtao"]),
        "avatar": row["avatar"],
        "crewTag": row["crew_tag"] or "",
        "lastSeen": row["last_seen"] or ""
    }

@router.get("/friends")
@router.get("/gwsc/api/friends")
async def get_friends(current_user: Dict[str, Any] = Depends(get_current_user)):
    conn = get_db()
    cursor = conn.cursor()

    # Accepted friends
    cursor.execute("""
        SELECT u.* FROM users u
        JOIN friends f ON (
            (f.user_id = ? AND f.friend_id = u.id) OR
            (f.friend_id = ? AND f.user_id = u.id)
        )
        WHERE f.status = 'accepted'
    """, (current_user["id"], current_user["id"]))
    friends_list = [format_friend_item(r) for r in cursor.fetchall()]

    # Incoming requests (other user sent request to me)
    cursor.execute("""
        SELECT u.* FROM users u
        JOIN friends f ON f.user_id = u.id AND f.friend_id = ?
        WHERE f.status = 'pending_outgoing'
    """, (current_user["id"],))
    incoming_list = [format_friend_item(r) for r in cursor.fetchall()]

    # Outgoing requests (I sent request to other user)
    cursor.execute("""
        SELECT u.* FROM users u
        JOIN friends f ON f.friend_id = u.id AND f.user_id = ?
        WHERE f.status = 'pending_outgoing'
    """, (current_user["id"],))
    outgoing_list = [format_friend_item(r) for r in cursor.fetchall()]

    # Blocked users
    cursor.execute("""
        SELECT u.* FROM users u
        JOIN friends f ON f.friend_id = u.id AND f.user_id = ?
        WHERE f.status = 'blocked'
    """, (current_user["id"],))
    blocked_list = [format_friend_item(r) for r in cursor.fetchall()]

    conn.close()

    return {
        "friends": friends_list,
        "incoming": incoming_list,
        "outgoing": outgoing_list,
        "blocked": blocked_list
    }

@router.post("/friends")
@router.post("/gwsc/api/friends")
async def add_friend(req: AddFriendRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    target_nick = req.nickname.strip()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE LOWER(nickname) = LOWER(?)", (target_nick,))
    target = cursor.fetchone()
    if not target:
        conn.close()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "user_not_found", "message": f"User '{target_nick}' was not found"}
        )

    target_id = target["id"]
    if target_id == current_user["id"]:
        conn.close()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "self_request", "message": "You cannot add yourself as a friend"}
        )

    # Check existing relation
    cursor.execute("""
        SELECT * FROM friends
        WHERE (user_id = ? AND friend_id = ?) OR (user_id = ? AND friend_id = ?)
    """, (current_user["id"], target_id, target_id, current_user["id"]))
    existing = cursor.fetchone()

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if existing:
        if existing["status"] == "accepted":
            conn.close()
            return {"status": "ok", "message": "Already friends"}
        elif existing["status"] == "pending_outgoing" and existing["user_id"] == target_id:
            # The other user had already sent an invite to me, auto-accept!
            with conn:
                conn.execute("UPDATE friends SET status = 'accepted' WHERE id = ?", (existing["id"],))
            conn.close()
            await event_bus.broadcast("friends", {"action": "accepted", "rockstarId": current_user["rockstar_id"]}, target_id)
            return {"status": "ok", "message": "Friend request accepted"}
        elif existing["status"] == "blocked":
            conn.close()
            raise HTTPException(status_code=400, detail={"error": "blocked", "message": "Cannot add blocked user"})

    # Insert pending request
    with conn:
        conn.execute("""
            INSERT INTO friends (user_id, friend_id, status, created_at)
            VALUES (?, ?, 'pending_outgoing', ?)
            ON CONFLICT(user_id, friend_id) DO UPDATE SET status = 'pending_outgoing'
        """, (current_user["id"], target_id, now))
    conn.close()

    # Notify target user via SSE
    await event_bus.broadcast("friends", {
        "action": "incoming_request",
        "rockstarId": current_user["rockstar_id"],
        "nickname": current_user["nickname"]
    }, target_id)

    return {"status": "ok", "message": "Friend request sent"}

@router.post("/friends/{target_id}/accept")
@router.post("/gwsc/api/friends/{target_id}/accept")
async def accept_friend(target_id: int, current_user: Dict[str, Any] = Depends(get_current_user)):
    conn = get_db()
    cursor = conn.cursor()
    # Resolve rockstar_id or db id
    cursor.execute("SELECT id, rockstar_id FROM users WHERE rockstar_id = ? OR id = ?", (target_id, target_id))
    t_row = cursor.fetchone()
    if not t_row:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "user_not_found", "message": "User not found"})

    other_user_id = t_row["id"]
    with conn:
        conn.execute("""
            UPDATE friends SET status = 'accepted'
            WHERE (user_id = ? AND friend_id = ?) OR (user_id = ? AND friend_id = ?)
        """, (other_user_id, current_user["id"], current_user["id"], other_user_id))
    conn.close()

    await event_bus.broadcast("friends", {
        "action": "accepted",
        "rockstarId": current_user["rockstar_id"],
        "nickname": current_user["nickname"]
    }, other_user_id)

    return {"status": "ok"}

@router.delete("/friends/{target_id}")
@router.delete("/gwsc/api/friends/{target_id}")
async def delete_friend(target_id: int, current_user: Dict[str, Any] = Depends(get_current_user)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, rockstar_id FROM users WHERE rockstar_id = ? OR id = ?", (target_id, target_id))
    t_row = cursor.fetchone()
    if not t_row:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "user_not_found", "message": "User not found"})

    other_user_id = t_row["id"]
    with conn:
        conn.execute("""
            DELETE FROM friends
            WHERE (user_id = ? AND friend_id = ?) OR (user_id = ? AND friend_id = ?)
        """, (current_user["id"], other_user_id, other_user_id, current_user["id"]))
    conn.close()

    await event_bus.broadcast("friends", {
        "action": "removed",
        "rockstarId": current_user["rockstar_id"]
    }, other_user_id)

    return {"status": "ok"}
