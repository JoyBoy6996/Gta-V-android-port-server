from fastapi import APIRouter
from ..config import SERVER_NAME, SERVER_VERSION, REGISTRATION_ENABLED
from ..database import get_db

router = APIRouter(tags=["info"])

@router.get("/info")
@router.get("/gwsc/api/info")
async def server_info():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    total_accounts = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE is_online = 1")
    online_count = cursor.fetchone()[0]
    conn.close()

    # The game expects at least 1 online user to show active status
    display_online = max(1, online_count)

    return {
        "name": SERVER_NAME,
        "version": SERVER_VERSION,
        "server": SERVER_NAME,
        "status": "online",
        "accounts": total_accounts,
        "online": display_online,
        "registration": REGISTRATION_ENABLED
    }
