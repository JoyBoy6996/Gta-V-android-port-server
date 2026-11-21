import logging
from contextlib import closing
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from .. import config
from ..auth import get_current_user
from ..database import get_db
from ..errors import api_error
from ..security import rate_limiter
from ..time_utils import now_iso
from ..validators import clean_message_body
from .events import event_bus

log = logging.getLogger("gwsc.messages")
router = APIRouter(tags=["messages"])


class CreateConversationRequest(BaseModel):
    rockstarId: Optional[int] = None
    name: Optional[str] = None
    rockstarIds: Optional[List[int]] = None


class SendMessageRequest(BaseModel):
    body: str


class MarkReadRequest(BaseModel):
    lastId: Optional[int] = 0


def _is_blocked(conn, first_id: int, second_id: int) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM friends
        WHERE status = 'blocked' AND (
            (user_id = ? AND friend_id = ?) OR (user_id = ? AND friend_id = ?)
        )
        """,
        (first_id, second_id, second_id, first_id),
    ).fetchone()
    return row is not None


def _membership(conn, conversation_id: int, user_id: int):
    return conn.execute(
        "SELECT * FROM conversation_members WHERE conversation_id = ? AND user_id = ?",
        (conversation_id, user_id),
    ).fetchone()


@router.get("/conversations")
@router.get("/gwsc/api/conversations")
async def get_conversations(current_user: Dict[str, Any] = Depends(get_current_user)):
    with closing(get_db()) as conn:
        conv_rows = conn.execute(
            """
            SELECT c.id, c.kind, c.name, c.created_by, cm.last_read_message_id
            FROM conversations c
            JOIN conversation_members cm ON c.id = cm.conversation_id
            WHERE cm.user_id = ?
            ORDER BY c.id DESC
            LIMIT ?
            """,
            (current_user["id"], config.MAX_CONVERSATIONS_PER_REQUEST),
        ).fetchall()

        conversations = []
        for conversation in conv_rows:
            cid = conversation["id"]
            members = [
                {
                    "rockstarId": member["rockstar_id"],
                    "nickname": member["nickname"],
                    "avatar": member["avatar"],
                    "online": bool(member["is_online"]),
                }
                for member in conn.execute(
                    """
                    SELECT u.rockstar_id, u.nickname, u.avatar, u.is_online
                    FROM conversation_members cm
                    JOIN users u ON cm.user_id = u.id
                    WHERE cm.conversation_id = ?
                    """,
                    (cid,),
                ).fetchall()
            ]
            last_message = conn.execute(
                """
                SELECT m.id, m.body, m.created_at, u.rockstar_id, u.nickname
                FROM messages m
                JOIN users u ON m.sender_id = u.id
                WHERE m.conversation_id = ?
                ORDER BY m.id DESC LIMIT 1
                """,
                (cid,),
            ).fetchone()
            last_read_id = conversation["last_read_message_id"] or 0
            unread = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND id > ?",
                (cid, last_read_id),
            ).fetchone()[0]

            display_name = conversation["name"]
            if conversation["kind"] == "direct":
                for member in members:
                    if member["rockstarId"] != current_user["rockstar_id"]:
                        display_name = member["nickname"]
                        break

            conversations.append(
                {
                    "id": cid,
                    "kind": conversation["kind"],
                    "name": display_name,
                    "members": members,
                    "lastMessage": (
                        {
                            "id": last_message["id"],
                            "body": last_message["body"],
                            "createdAt": last_message["created_at"],
                            "sender": {
                                "rockstarId": last_message["rockstar_id"],
                                "nickname": last_message["nickname"],
                            },
                        }
                        if last_message
                        else None
                    ),
                    "unread": unread,
                }
            )

    return {"conversations": conversations}


@router.post("/conversations")
@router.post("/gwsc/api/conversations")
async def create_conversation(
    req: CreateConversationRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    now = now_iso()
    with closing(get_db()) as conn:
        if req.rockstarId is not None:
            target = conn.execute(
                "SELECT * FROM users WHERE rockstar_id = ?", (req.rockstarId,)
            ).fetchone()
            if not target or target["status"] != "active":
                raise api_error(404, "user_not_found", "User not found")
            if target["id"] == current_user["id"]:
                raise api_error(400, "self_conversation", "Cannot create a conversation with yourself")
            if _is_blocked(conn, current_user["id"], target["id"]):
                raise api_error(403, "blocked", "Cannot message this user")

            existing = conn.execute(
                """
                SELECT cm1.conversation_id FROM conversation_members cm1
                JOIN conversation_members cm2 ON cm1.conversation_id = cm2.conversation_id
                JOIN conversations c ON c.id = cm1.conversation_id
                WHERE c.kind = 'direct' AND cm1.user_id = ? AND cm2.user_id = ?
                """,
                (current_user["id"], target["id"]),
            ).fetchone()
            if existing:
                return {"id": existing[0], "kind": "direct", "name": target["nickname"]}

            with conn:
                cursor = conn.execute(
                    "INSERT INTO conversations (kind, name, created_by, created_at) "
                    "VALUES ('direct', '', ?, ?)",
                    (current_user["id"], now),
                )
                cid = cursor.lastrowid
                conn.execute(
                    "INSERT INTO conversation_members (conversation_id, user_id, joined_at) "
                    "VALUES (?, ?, ?)",
                    (cid, current_user["id"], now),
                )
                conn.execute(
                    "INSERT INTO conversation_members (conversation_id, user_id, joined_at) "
                    "VALUES (?, ?, ?)",
                    (cid, target["id"], now),
                )
            return {"id": cid, "kind": "direct", "name": target["nickname"]}

        if req.rockstarIds:
            requested = []
            for rockstar_id in req.rockstarIds:
                if rockstar_id == current_user["rockstar_id"]:
                    continue
                member = conn.execute(
                    "SELECT id FROM users WHERE rockstar_id = ? AND status = 'active'",
                    (rockstar_id,),
                ).fetchone()
                if member:
                    requested.append(member["id"])
            requested = list(dict.fromkeys(requested))
            if not requested:
                raise api_error(400, "invalid_members", "No valid members supplied")
            if len(requested) + 1 > config.MAX_GROUP_MEMBERS:
                raise api_error(400, "too_many_members", "Group conversation is too large")
            for member_id in requested:
                if _is_blocked(conn, current_user["id"], member_id):
                    raise api_error(403, "blocked", "Cannot add a blocked user")

            group_name = (req.name or "Group Chat").strip()[:80] or "Group Chat"
            with conn:
                cursor = conn.execute(
                    "INSERT INTO conversations (kind, name, created_by, created_at) "
                    "VALUES ('group', ?, ?, ?)",
                    (group_name, current_user["id"], now),
                )
                cid = cursor.lastrowid
                conn.execute(
                    "INSERT INTO conversation_members (conversation_id, user_id, joined_at) "
                    "VALUES (?, ?, ?)",
                    (cid, current_user["id"], now),
                )
                for member_id in requested:
                    conn.execute(
                        "INSERT OR IGNORE INTO conversation_members "
                        "(conversation_id, user_id, joined_at) VALUES (?, ?, ?)",
                        (cid, member_id, now),
                    )
            return {"id": cid, "kind": "group", "name": group_name}

    raise api_error(400, "invalid_request", "Specify rockstarId or rockstarIds")


@router.get("/conversations/{cid}/messages")
@router.get("/gwsc/api/conversations/{cid}/messages")
async def get_messages(
    cid: int,
    limit: int = Query(50, ge=1, le=config.MAX_MESSAGES_PAGE_SIZE),
    before_id: Optional[int] = Query(None, ge=1),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    with closing(get_db()) as conn:
        if not _membership(conn, cid, current_user["id"]):
            raise api_error(403, "not_member", "Not a member of this conversation")

        conversation = conn.execute(
            "SELECT id, kind FROM conversations WHERE id = ?", (cid,)
        ).fetchone()
        if conversation and conversation["kind"] == "direct":
            other = conn.execute(
                "SELECT user_id FROM conversation_members WHERE conversation_id = ? AND user_id != ?",
                (cid, current_user["id"]),
            ).fetchone()
            if other and _is_blocked(conn, current_user["id"], other["user_id"]):
                raise api_error(403, "blocked", "This conversation is not available")

        if before_id:
            rows = conn.execute(
                """
                SELECT m.id, m.body, m.created_at, u.rockstar_id, u.nickname, u.avatar
                FROM messages m
                JOIN users u ON m.sender_id = u.id
                WHERE m.conversation_id = ? AND m.id < ?
                ORDER BY m.id DESC LIMIT ?
                """,
                (cid, before_id, limit),
            ).fetchall()
            rows = list(reversed(rows))
        else:
            rows = conn.execute(
                """
                SELECT m.id, m.body, m.created_at, u.rockstar_id, u.nickname, u.avatar
                FROM messages m
                JOIN users u ON m.sender_id = u.id
                WHERE m.conversation_id = ?
                ORDER BY m.id DESC LIMIT ?
                """,
                (cid, limit),
            ).fetchall()
            rows = list(reversed(rows))

    return {
        "messages": [
            {
                "id": row["id"],
                "body": row["body"],
                "createdAt": row["created_at"],
                "sender": {
                    "rockstarId": row["rockstar_id"],
                    "nickname": row["nickname"],
                    "avatar": row["avatar"],
                },
            }
            for row in rows
        ]
    }


@router.post("/conversations/{cid}/messages")
@router.post("/gwsc/api/conversations/{cid}/messages")
async def send_message(
    cid: int,
    req: SendMessageRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    body = clean_message_body(req.body)
    if not body:
        raise api_error(
            400,
            "invalid_body",
            f"Message must be 1-{config.MAX_MESSAGE_LENGTH} characters",
        )

    allowed, retry_after = rate_limiter.check(
        f"message:{current_user['id']}",
        config.MESSAGE_RATE_LIMIT,
        config.MESSAGE_RATE_WINDOW,
    )
    if not allowed:
        raise api_error(
            429,
            "rate_limited",
            "Too many messages. Slow down.",
            headers={"Retry-After": str(retry_after)},
        )

    now = now_iso()
    with closing(get_db()) as conn:
        if not _membership(conn, cid, current_user["id"]):
            raise api_error(403, "not_member", "Not a member of this conversation")
        conversation = conn.execute(
            "SELECT id, kind, name FROM conversations WHERE id = ?", (cid,)
        ).fetchone()
        if not conversation:
            raise api_error(404, "conversation_not_found", "Conversation not found")

        other_members = [
            row[0]
            for row in conn.execute(
                "SELECT user_id FROM conversation_members WHERE conversation_id = ? AND user_id != ?",
                (cid, current_user["id"]),
            ).fetchall()
        ]
        if conversation["kind"] == "direct":
            for member_id in other_members:
                if _is_blocked(conn, current_user["id"], member_id):
                    raise api_error(403, "blocked", "Cannot message this user")

        with conn:
            cursor = conn.execute(
                "INSERT INTO messages (conversation_id, sender_id, body, created_at) "
                "VALUES (?, ?, ?, ?)",
                (cid, current_user["id"], body, now),
            )
            message_id = cursor.lastrowid
            conn.execute(
                "UPDATE conversation_members SET last_read_message_id = ? "
                "WHERE conversation_id = ? AND user_id = ?",
                (message_id, cid, current_user["id"]),
            )

    event_payload = {
        "kind": "group" if conversation["kind"] == "group" else "system",
        "rockstarId": current_user["rockstar_id"],
        "sender": {
            "rockstarId": current_user["rockstar_id"],
            "nickname": current_user["nickname"],
        },
        "conversationId": cid,
        "nickname": current_user["nickname"],
        "conversationKind": conversation["kind"],
        "conversationName": conversation["name"],
        "body": body,
        "messageId": message_id,
        "createdAt": now,
    }
    for member_id in other_members:
        await event_bus.broadcast("message", event_payload, member_id)

    return {
        "id": message_id,
        "body": body,
        "createdAt": now,
        "sender": {
            "rockstarId": current_user["rockstar_id"],
            "nickname": current_user["nickname"],
        },
    }


@router.post("/conversations/{cid}/read")
@router.post("/gwsc/api/conversations/{cid}/read")
async def mark_read(
    cid: int,
    req: MarkReadRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    with closing(get_db()) as conn:
        if not _membership(conn, cid, current_user["id"]):
            raise api_error(403, "not_member", "Not a member of this conversation")
        with conn:
            if req.lastId and req.lastId > 0:
                conn.execute(
                    """
                    UPDATE conversation_members
                    SET last_read_message_id = MAX(last_read_message_id, ?)
                    WHERE conversation_id = ? AND user_id = ?
                    """,
                    (req.lastId, cid, current_user["id"]),
                )
            else:
                conn.execute(
                    """
                    UPDATE conversation_members
                    SET last_read_message_id =
                        (SELECT IFNULL(MAX(id), 0) FROM messages WHERE conversation_id = ?)
                    WHERE conversation_id = ? AND user_id = ?
                    """,
                    (cid, cid, current_user["id"]),
                )
    return {"status": "ok"}


@router.delete("/conversations/{cid}/members/{rockstar_id}")
@router.delete("/gwsc/api/conversations/{cid}/members/{rockstar_id}")
async def remove_member(
    cid: int,
    rockstar_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    with closing(get_db()) as conn:
        conversation = conn.execute(
            "SELECT id, kind, created_by FROM conversations WHERE id = ?", (cid,)
        ).fetchone()
        if not conversation:
            raise api_error(404, "conversation_not_found", "Conversation not found")
        if conversation["kind"] != "group":
            raise api_error(400, "direct_conversation", "Direct conversations cannot remove members")
        if not _membership(conn, cid, current_user["id"]):
            raise api_error(403, "not_member", "Not a member of this conversation")

        target = conn.execute(
            "SELECT id FROM users WHERE rockstar_id = ?", (rockstar_id,)
        ).fetchone()
        if not target:
            raise api_error(404, "user_not_found", "User not found")
        if target["id"] != current_user["id"] and conversation["created_by"] != current_user["id"]:
            raise api_error(403, "forbidden", "Only the conversation owner can remove members")
        if not _membership(conn, cid, target["id"]):
            raise api_error(404, "member_not_found", "User is not in this conversation")

        with conn:
            conn.execute(
                "DELETE FROM conversation_members WHERE conversation_id = ? AND user_id = ?",
                (cid, target["id"]),
            )
    return {"status": "ok"}
