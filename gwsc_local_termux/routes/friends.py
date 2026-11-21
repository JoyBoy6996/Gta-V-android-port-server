import logging
import sqlite3
from contextlib import closing
from typing import Any, Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth import get_current_user
from ..database import get_db
from ..errors import api_error
from ..time_utils import now_iso
from ..validators import validate_nickname
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
        "lastSeen": row["last_seen"] or "",
    }


def _resolve_by_rockstar_id(conn, rockstar_id: int):
    return conn.execute(
        "SELECT * FROM users WHERE rockstar_id = ?", (rockstar_id,)
    ).fetchone()


@router.get("/friends")
@router.get("/gwsc/api/friends")
async def get_friends(current_user: Dict[str, Any] = Depends(get_current_user)):
    with closing(get_db()) as conn:
        friends_list = [
            format_friend_item(row)
            for row in conn.execute(
                """
                SELECT u.* FROM users u
                JOIN friends f ON (
                    (f.user_id = ? AND f.friend_id = u.id) OR
                    (f.friend_id = ? AND f.user_id = u.id)
                )
                WHERE f.status = 'accepted'
                """,
                (current_user["id"], current_user["id"]),
            ).fetchall()
        ]
        incoming_list = [
            format_friend_item(row)
            for row in conn.execute(
                """
                SELECT u.* FROM users u
                JOIN friends f ON f.user_id = u.id AND f.friend_id = ?
                WHERE f.status = 'pending'
                """,
                (current_user["id"],),
            ).fetchall()
        ]
        outgoing_list = [
            format_friend_item(row)
            for row in conn.execute(
                """
                SELECT u.* FROM users u
                JOIN friends f ON f.friend_id = u.id AND f.user_id = ?
                WHERE f.status = 'pending'
                """,
                (current_user["id"],),
            ).fetchall()
        ]
        blocked_list = [
            format_friend_item(row)
            for row in conn.execute(
                """
                SELECT u.* FROM users u
                JOIN friends f ON f.friend_id = u.id AND f.user_id = ?
                WHERE f.status = 'blocked'
                """,
                (current_user["id"],),
            ).fetchall()
        ]

    return {
        "friends": friends_list,
        "incoming": incoming_list,
        "outgoing": outgoing_list,
        "blocked": blocked_list,
    }


@router.post("/friends")
@router.post("/gwsc/api/friends")
async def add_friend(
    req: AddFriendRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    nickname = validate_nickname(req.nickname)
    if not nickname:
        raise api_error(400, "invalid_nickname", "A valid nickname is required")

    now = now_iso()
    accepted_now = False
    with closing(get_db()) as conn:
        target = conn.execute(
            "SELECT * FROM users WHERE LOWER(nickname) = LOWER(?)", (nickname,)
        ).fetchone()
        if not target or target["status"] != "active":
            raise api_error(404, "user_not_found", f"User '{nickname}' was not found")
        target_id = target["id"]
        if target_id == current_user["id"]:
            raise api_error(400, "self_request", "You cannot add yourself as a friend")

        existing = conn.execute(
            """
            SELECT * FROM friends
            WHERE (user_id = ? AND friend_id = ?) OR (user_id = ? AND friend_id = ?)
            """,
            (current_user["id"], target_id, target_id, current_user["id"]),
        ).fetchone()

        with conn:
            if existing:
                if existing["status"] == "accepted":
                    return {"status": "ok", "message": "Already friends"}
                if existing["status"] == "blocked":
                    if existing["user_id"] == current_user["id"]:
                        raise api_error(400, "blocked", "Unblock this user first")
                    raise api_error(404, "user_not_found", f"User '{nickname}' was not found")
                if existing["status"] == "pending":
                    if existing["user_id"] == target_id:
                        # They already invited us; accept it.
                        conn.execute(
                            "UPDATE friends SET status = 'accepted', updated_at = ? WHERE id = ?",
                            (now, existing["id"]),
                        )
                        accepted_now = True
                    else:
                        return {"status": "ok", "message": "Friend request already sent"}
                else:
                    conn.execute(
                        "UPDATE friends SET status = 'pending', updated_at = ? WHERE id = ?",
                        (now, existing["id"]),
                    )
            else:
                conn.execute(
                    """
                    INSERT INTO friends (user_id, friend_id, status, created_at, updated_at)
                    VALUES (?, ?, 'pending', ?, ?)
                    """,
                    (current_user["id"], target_id, now, now),
                )

    if accepted_now:
        await event_bus.broadcast(
            "friends",
            {"action": "accepted", "rockstarId": current_user["rockstar_id"]},
            target_id,
        )
        return {"status": "ok", "message": "Friend request accepted"}

    await event_bus.broadcast(
        "friends",
        {
            "action": "incoming_request",
            "rockstarId": current_user["rockstar_id"],
            "nickname": current_user["nickname"],
        },
        target_id,
    )
    return {"status": "ok", "message": "Friend request sent"}


@router.post("/friends/{target_id}/accept")
@router.post("/gwsc/api/friends/{target_id}/accept")
async def accept_friend(
    target_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    with closing(get_db()) as conn:
        target = _resolve_by_rockstar_id(conn, target_id)
        if not target:
            raise api_error(404, "user_not_found", "User not found")
        pending = conn.execute(
            """
            SELECT id FROM friends
            WHERE user_id = ? AND friend_id = ? AND status = 'pending'
            """,
            (target["id"], current_user["id"]),
        ).fetchone()
        if not pending:
            raise api_error(404, "request_not_found", "No incoming friend request found")
        with conn:
            conn.execute(
                "UPDATE friends SET status = 'accepted', updated_at = ? WHERE id = ?",
                (now_iso(), pending["id"]),
            )

    await event_bus.broadcast(
        "friends",
        {
            "action": "accepted",
            "rockstarId": current_user["rockstar_id"],
            "nickname": current_user["nickname"],
        },
        target["id"],
    )
    return {"status": "ok"}


@router.post("/friends/{target_id}/reject")
@router.post("/gwsc/api/friends/{target_id}/reject")
async def reject_friend(
    target_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    with closing(get_db()) as conn:
        target = _resolve_by_rockstar_id(conn, target_id)
        if not target:
            raise api_error(404, "user_not_found", "User not found")
        pending = conn.execute(
            """
            SELECT id FROM friends
            WHERE user_id = ? AND friend_id = ? AND status = 'pending'
            """,
            (target["id"], current_user["id"]),
        ).fetchone()
        if not pending:
            raise api_error(404, "request_not_found", "No incoming friend request found")
        with conn:
            conn.execute(
                "UPDATE friends SET status = 'rejected', updated_at = ? WHERE id = ?",
                (now_iso(), pending["id"]),
            )

    await event_bus.broadcast(
        "friends",
        {"action": "rejected", "rockstarId": current_user["rockstar_id"]},
        target["id"],
    )
    return {"status": "ok"}


@router.delete("/friends/{target_id}")
@router.delete("/gwsc/api/friends/{target_id}")
async def delete_friend(
    target_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    with closing(get_db()) as conn:
        target = _resolve_by_rockstar_id(conn, target_id)
        if not target:
            raise api_error(404, "user_not_found", "User not found")
        relation = conn.execute(
            """
            SELECT id, status FROM friends
            WHERE (user_id = ? AND friend_id = ?) OR (user_id = ? AND friend_id = ?)
            """,
            (current_user["id"], target["id"], target["id"], current_user["id"]),
        ).fetchone()
        if not relation:
            raise api_error(404, "relationship_not_found", "No relationship found")
        if relation["status"] == "blocked":
            raise api_error(400, "blocked", "Unblock this user instead of deleting the relationship")
        with conn:
            conn.execute("DELETE FROM friends WHERE id = ?", (relation["id"],))

    await event_bus.broadcast(
        "friends",
        {"action": "removed", "rockstarId": current_user["rockstar_id"]},
        target["id"],
    )
    return {"status": "ok"}
