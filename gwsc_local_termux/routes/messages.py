import datetime
import logging
from typing import Optional, Dict, Any, List
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Depends, status
from ..database import get_db
from ..auth import get_current_user
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

@router.get("/conversations")
@router.get("/gwsc/api/conversations")
async def get_conversations(current_user: Dict[str, Any] = Depends(get_current_user)):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT c.id, c.kind, c.name, cm.last_read_message_id
        FROM conversations c
        JOIN conversation_members cm ON c.id = cm.conversation_id
        WHERE cm.user_id = ?
        ORDER BY c.id DESC
    """, (current_user["id"],))
    conv_rows = cursor.fetchall()

    conversations = []
    for c in conv_rows:
        cid = c["id"]
        # Fetch members
        cursor.execute("""
            SELECT u.rockstar_id, u.nickname, u.avatar, u.is_online
            FROM conversation_members cm
            JOIN users u ON cm.user_id = u.id
            WHERE cm.conversation_id = ?
        """, (cid,))
        members = [
            {
                "rockstarId": m["rockstar_id"],
                "nickname": m["nickname"],
                "avatar": m["avatar"],
                "online": bool(m["is_online"])
            }
            for m in cursor.fetchall()
        ]

        # Fetch last message
        cursor.execute("""
            SELECT m.id, m.body, m.created_at, u.rockstar_id, u.nickname
            FROM messages m
            JOIN users u ON m.sender_id = u.id
            WHERE m.conversation_id = ?
            ORDER BY m.id DESC LIMIT 1
        """, (cid,))
        last_msg_row = cursor.fetchone()
        last_msg = None
        if last_msg_row:
            last_msg = {
                "id": last_msg_row["id"],
                "body": last_msg_row["body"],
                "createdAt": last_msg_row["created_at"],
                "sender": {
                    "rockstarId": last_msg_row["rockstar_id"],
                    "nickname": last_msg_row["nickname"]
                }
            }

        # Calculate unread
        last_read_id = c["last_read_message_id"] or 0
        cursor.execute("""
            SELECT COUNT(*) FROM messages
            WHERE conversation_id = ? AND id > ?
        """, (cid, last_read_id))
        unread_count = cursor.fetchone()[0]

        # For direct conversations, name is the other participant's nickname
        display_name = c["name"]
        if c["kind"] == "direct":
            for m in members:
                if m["rockstarId"] != current_user["rockstar_id"]:
                    display_name = m["nickname"]
                    break

        conversations.append({
            "id": cid,
            "kind": c["kind"],
            "name": display_name,
            "members": members,
            "lastMessage": last_msg,
            "unread": unread_count
        })

    conn.close()
    return {"conversations": conversations}

@router.post("/conversations")
@router.post("/gwsc/api/conversations")
async def create_conversation(req: CreateConversationRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if req.rockstarId is not None:
        # Direct conversation
        cursor.execute("SELECT id, nickname, rockstar_id FROM users WHERE rockstar_id = ?", (req.rockstarId,))
        target = cursor.fetchone()
        if not target:
            conn.close()
            raise HTTPException(status_code=404, detail={"error": "user_not_found", "message": "User not found"})

        target_id = target["id"]

        # Check if direct conversation already exists between these 2 users
        cursor.execute("""
            SELECT cm1.conversation_id FROM conversation_members cm1
            JOIN conversation_members cm2 ON cm1.conversation_id = cm2.conversation_id
            JOIN conversations c ON c.id = cm1.conversation_id
            WHERE c.kind = 'direct' AND cm1.user_id = ? AND cm2.user_id = ?
        """, (current_user["id"], target_id))
        existing = cursor.fetchone()
        if existing:
            cid = existing[0]
            conn.close()
            return {"id": cid, "kind": "direct", "name": target["nickname"]}

        with conn:
            cursor.execute("INSERT INTO conversations (kind, name, created_at) VALUES ('direct', '', ?)", (now,))
            cid = cursor.lastrowid
            cursor.execute("INSERT INTO conversation_members (conversation_id, user_id) VALUES (?, ?)", (cid, current_user["id"]))
            cursor.execute("INSERT INTO conversation_members (conversation_id, user_id) VALUES (?, ?)", (cid, target_id))
        conn.close()
        return {"id": cid, "kind": "direct", "name": target["nickname"]}

    elif req.rockstarIds:
        # Group conversation
        group_name = req.name or "Group Chat"
        with conn:
            cursor.execute("INSERT INTO conversations (kind, name, created_at) VALUES ('group', ?, ?)", (group_name, now))
            cid = cursor.lastrowid
            cursor.execute("INSERT INTO conversation_members (conversation_id, user_id) VALUES (?, ?)", (cid, current_user["id"]))
            for r_id in req.rockstarIds:
                cursor.execute("SELECT id FROM users WHERE rockstar_id = ?", (r_id,))
                urow = cursor.fetchone()
                if urow and urow[0] != current_user["id"]:
                    cursor.execute("INSERT OR IGNORE INTO conversation_members (conversation_id, user_id) VALUES (?, ?)", (cid, urow[0]))
        conn.close()
        return {"id": cid, "kind": "group", "name": group_name}

    conn.close()
    raise HTTPException(status_code=400, detail={"error": "invalid_request", "message": "Specify rockstarId or rockstarIds"})

@router.get("/conversations/{cid}/messages")
@router.get("/gwsc/api/conversations/{cid}/messages")
async def get_messages(cid: int, current_user: Dict[str, Any] = Depends(get_current_user)):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM conversation_members WHERE conversation_id = ? AND user_id = ?", (cid, current_user["id"]))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=403, detail={"error": "not_member", "message": "Not a member of this conversation"})

    cursor.execute("""
        SELECT m.id, m.body, m.created_at, u.rockstar_id, u.nickname, u.avatar
        FROM messages m
        JOIN users u ON m.sender_id = u.id
        WHERE m.conversation_id = ?
        ORDER BY m.id ASC
    """, (cid,))
    rows = cursor.fetchall()

    messages = [
        {
            "id": r["id"],
            "body": r["body"],
            "createdAt": r["created_at"],
            "sender": {
                "rockstarId": r["rockstar_id"],
                "nickname": r["nickname"],
                "avatar": r["avatar"]
            }
        }
        for r in rows
    ]
    conn.close()
    return {"messages": messages}

@router.post("/conversations/{cid}/messages")
@router.post("/gwsc/api/conversations/{cid}/messages")
async def send_message(cid: int, req: SendMessageRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    body = req.body.strip()
    if not body:
        raise HTTPException(status_code=400, detail={"error": "empty_body", "message": "Message body cannot be empty"})

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, kind, name FROM conversations WHERE id = ?", (cid,))
    conv = cursor.fetchone()
    if not conv:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "conversation_not_found", "message": "Conversation not found"})

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with conn:
        cursor.execute("INSERT INTO messages (conversation_id, sender_id, body, created_at) VALUES (?, ?, ?, ?)",
                       (cid, current_user["id"], body, now))
        msg_id = cursor.lastrowid
        cursor.execute("UPDATE conversation_members SET last_read_message_id = ? WHERE conversation_id = ? AND user_id = ?",
                       (msg_id, cid, current_user["id"]))

    # Find all members to broadcast SSE event
    cursor.execute("SELECT user_id FROM conversation_members WHERE conversation_id = ?", (cid,))
    member_user_ids = [r[0] for r in cursor.fetchall()]
    conn.close()

    event_payload = {
        "kind": "group" if conv["kind"] == "group" else "system",
        "rockstarId": current_user["rockstar_id"],
        "sender": {
            "rockstarId": current_user["rockstar_id"],
            "nickname": current_user["nickname"]
        },
        "conversationId": cid,
        "nickname": current_user["nickname"],
        "conversationKind": conv["kind"],
        "conversationName": conv["name"],
        "body": body,
        "messageId": msg_id,
        "createdAt": now
    }

    for uid in member_user_ids:
        if uid != current_user["id"]:
            await event_bus.broadcast("message", event_payload, uid)

    return {
        "id": msg_id,
        "body": body,
        "createdAt": now,
        "sender": {
            "rockstarId": current_user["rockstar_id"],
            "nickname": current_user["nickname"]
        }
    }

@router.post("/conversations/{cid}/read")
@router.post("/gwsc/api/conversations/{cid}/read")
async def mark_read(cid: int, req: MarkReadRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    last_id = req.lastId or 0
    conn = get_db()
    with conn:
        if last_id > 0:
            conn.execute("UPDATE conversation_members SET last_read_message_id = MAX(last_read_message_id, ?) WHERE conversation_id = ? AND user_id = ?",
                         (last_id, cid, current_user["id"]))
        else:
            conn.execute("""
                UPDATE conversation_members
                SET last_read_message_id = (SELECT IFNULL(MAX(id), 0) FROM messages WHERE conversation_id = ?)
                WHERE conversation_id = ? AND user_id = ?
            """, (cid, cid, current_user["id"]))
    conn.close()
    return {"status": "ok"}

@router.delete("/conversations/{cid}/members/{rockstar_id}")
@router.delete("/gwsc/api/conversations/{cid}/members/{rockstar_id}")
async def remove_member(cid: int, rockstar_id: int, current_user: Dict[str, Any] = Depends(get_current_user)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE rockstar_id = ?", (rockstar_id,))
    target = cursor.fetchone()
    if target:
        with conn:
            conn.execute("DELETE FROM conversation_members WHERE conversation_id = ? AND user_id = ?", (cid, target["id"]))
    conn.close()
    return {"status": "ok"}
