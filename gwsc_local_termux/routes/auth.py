import datetime
import logging
from typing import Optional, Dict, Any
from pydantic import BaseModel, EmailStr
from fastapi import APIRouter, HTTPException, Depends, Header, status
from ..database import get_db, get_next_rockstar_id
from ..auth import hash_password, verify_password, create_user_token, revoke_token, revoke_all_user_tokens, get_current_user
from ..config import REGISTRATION_ENABLED
from .events import event_bus

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

@router.post("/register")
@router.post("/gwsc/api/register")
async def register(req: RegisterRequest):
    if not REGISTRATION_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "registration_disabled", "message": "User registration is currently closed"}
        )

    email = req.email.strip().lower()
    nickname = req.nickname.strip()
    password = req.password

    if len(nickname) < 3 or len(nickname) > 30:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_nickname", "message": "Nickname must be between 3 and 30 characters"}
        )
    if len(password) < 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_password", "message": "Password must be at least 4 characters"}
        )

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "email_exists", "message": "An account with this email already exists"}
        )

    cursor.execute("SELECT id FROM users WHERE LOWER(nickname) = LOWER(?)", (nickname,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "nickname_taken", "message": "This nickname is already in use"}
        )

    rockstar_id = get_next_rockstar_id(conn)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    pw_hash = hash_password(password)

    with conn:
        cursor.execute("""
            INSERT INTO users (rockstar_id, email, nickname, password_hash, created_at, last_seen, is_online)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        """, (rockstar_id, email, nickname, pw_hash, now, now))
        user_id = cursor.lastrowid
    conn.close()

    token = create_user_token(user_id)
    log.info("Registered new user: %s (Rockstar ID: %d)", nickname, rockstar_id)

    profile = {
        "rockstarId": rockstar_id,
        "nickname": nickname,
        "email": email,
        "avatar": "preset_0",
        "crewTag": "",
        "online": True
    }

    return {
        "token": token,
        "profile": profile
    }

@router.post("/login")
@router.post("/gwsc/api/login")
async def login(req: LoginRequest):
    email = req.email.strip().lower()
    password = req.password

    conn = get_db()
    cursor = conn.cursor()
    # Support logging in with email or nickname
    cursor.execute("""
        SELECT * FROM users
        WHERE email = ? OR LOWER(nickname) = ?
    """, (email, email))
    row = cursor.fetchone()

    if not row:
        conn.close()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_credentials", "message": "Invalid email or password"}
        )

    user = dict(row)
    if not verify_password(password, user["password_hash"]):
        conn.close()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_credentials", "message": "Invalid email or password"}
        )

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with conn:
        conn.execute("UPDATE users SET is_online = 1, last_seen = ? WHERE id = ?", (now, user["id"]))
    conn.close()

    token = create_user_token(user["id"])
    log.info("User logged in: %s (Rockstar ID: %d)", user["nickname"], user["rockstar_id"])

    profile = {
        "rockstarId": user["rockstar_id"],
        "nickname": user["nickname"],
        "email": user["email"],
        "avatar": user["avatar"],
        "crewTag": user["crew_tag"],
        "online": True
    }

    return {
        "token": token,
        "profile": profile
    }

@router.post("/logout")
@router.post("/gwsc/api/logout")
async def logout(authorization: Optional[str] = Header(None)):
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1]
            user = get_user_by_token(token)
            if user:
                conn = get_db()
                with conn:
                    conn.execute("UPDATE users SET is_online = 0 WHERE id = ?", (user["id"],))
                conn.close()
            revoke_token(token)
    return {"status": "ok"}

@router.post("/logout-all")
@router.post("/gwsc/api/logout-all")
async def logout_all(current_user: Dict[str, Any] = Depends(get_current_user)):
    conn = get_db()
    with conn:
        conn.execute("UPDATE users SET is_online = 0 WHERE id = ?", (current_user["id"],))
    conn.close()
    revoke_all_user_tokens(current_user["id"])
    return {"status": "ok"}
