from contextlib import closing

from fastapi import APIRouter

from .. import presence
from ..config import REGISTRATION_ENABLED, SERVER_NAME, SERVER_VERSION
from ..database import get_db

router = APIRouter(tags=["info"])


@router.get("/info")
@router.get("/gwsc/api/info")
async def server_info():
    with closing(get_db()) as conn:
        total_accounts = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        online_count = presence.online_count(conn)

    # Never fabricate an online player.  A zero count is a valid state.
    return {
        "name": SERVER_NAME,
        "version": SERVER_VERSION,
        "server": SERVER_NAME,
        "status": "online",
        "accounts": total_accounts,
        "online": online_count,
        "registration": REGISTRATION_ENABLED,
    }
