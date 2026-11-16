import hashlib
import secrets
import datetime
from typing import Optional, Dict, Any
from fastapi import Header, HTTPException, status
from .database import get_db
from .config import TOKEN_EXPIRY_SECONDS

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000)
    return f"{salt}:{dk.hex()}"

def verify_password(password: str, hashed: str) -> bool:
    try:
        salt, expected_hex = hashed.split(":", 1)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000)
        return secrets.compare_digest(dk.hex(), expected_hex)
    except Exception:
        return False

def create_user_token(user_id: int) -> str:
    token = "gwsc_" + secrets.token_urlsafe(32)
    now = datetime.datetime.now(datetime.timezone.utc)
    expires_at = now + datetime.timedelta(seconds=TOKEN_EXPIRY_SECONDS)
    conn = get_db()
    with conn:
        conn.execute(
            "INSERT INTO tokens (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, user_id, now.isoformat(), expires_at.isoformat())
        )
    conn.close()
    return token

def get_user_by_token(token: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT u.* FROM users u
        JOIN tokens t ON u.id = t.user_id
        WHERE t.token = ?
    """, (token,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def revoke_token(token: str) -> None:
    conn = get_db()
    with conn:
        conn.execute("DELETE FROM tokens WHERE token = ?", (token,))
    conn.close()

def revoke_all_user_tokens(user_id: int) -> None:
    conn = get_db()
    with conn:
        conn.execute("DELETE FROM tokens WHERE user_id = ?", (user_id,))
    conn.close()

async def get_current_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized", "message": "Missing Authorization header"}
        )
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized", "message": "Invalid Authorization header format"}
        )
    token = parts[1]
    user = get_user_by_token(token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized", "message": "Invalid or expired token"}
        )
    return user
