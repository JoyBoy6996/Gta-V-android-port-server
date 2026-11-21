"""Authenticated registration for the session-aware UDP relay."""

import logging
from contextlib import closing
from typing import Any, Dict

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from .. import config
from ..auth import get_current_user
from ..database import get_db
from ..errors import api_error
from ..multiplayer_relay import relay_registry
from ..request_utils import rate_key
from ..security import rate_limiter
from ..time_utils import now_iso

log = logging.getLogger("gwsc.relay_api")
router = APIRouter(tags=["relay"])


class RelayRegisterRequest(BaseModel):
    sessionId: str
    udpPort: int


def _session_membership(conn, session_id: str, user: Dict[str, Any]):
    session = conn.execute(
        "SELECT * FROM game_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if not session or not session["is_active"] or session["state"] == "closed":
        return None, None
    member = conn.execute(
        """
        SELECT * FROM session_members
        WHERE session_id = ? AND account_id = ? AND status = 'joined'
        """,
        (session_id, user["id"]),
    ).fetchone()
    return session, member


@router.post("/relay/register")
@router.post("/gwsc/api/relay/register")
async def register_relay_peer(
    req: RelayRegisterRequest,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    allowed, retry_after = rate_limiter.check(
        rate_key(request, "relay-register", str(current_user["id"])),
        config.RELAY_REGISTER_RATE_LIMIT,
        config.RELAY_REGISTER_RATE_WINDOW,
    )
    if not allowed:
        raise api_error(
            429,
            "rate_limited",
            "Too many relay registrations",
            headers={"Retry-After": str(retry_after)},
        )

    if not (1 <= req.udpPort <= 65535):
        raise api_error(400, "invalid_port", "UDP port must be between 1 and 65535")
    if len(req.sessionId) > 64 or not req.sessionId:
        raise api_error(400, "invalid_session", "Invalid session id")

    client_address = request.client.host if request.client else None
    if not client_address:
        raise api_error(400, "invalid_address", "Could not determine client address")

    with closing(get_db()) as conn:
        session, member = _session_membership(conn, req.sessionId, current_user)
        if not session:
            raise api_error(404, "session_not_found", "Session not found or inactive")
        if not member and session["host_account_id"] != current_user["id"]:
            raise api_error(403, "not_member", "You are not a member of this session")

        address = (client_address, req.udpPort)
        try:
            relay_registry.register(req.sessionId, current_user["id"], address)
        except ValueError:
            raise api_error(409, "relay_full", "Session relay is full")

        now = now_iso()
        with conn:
            conn.execute(
                """
                INSERT INTO relay_peers
                    (account_id, session_id, address, udp_port, registered_at, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(address, udp_port) DO UPDATE SET
                    account_id = excluded.account_id,
                    session_id = excluded.session_id,
                    registered_at = excluded.registered_at,
                    last_seen = excluded.last_seen
                """,
                (
                    current_user["id"],
                    req.sessionId,
                    client_address,
                    req.udpPort,
                    now,
                    now,
                ),
            )

    log.info(
        "Relay registration account=%s session=%s address=%s:%d",
        current_user["id"],
        req.sessionId,
        client_address,
        req.udpPort,
    )
    return {
        "status": "ok",
        "sessionId": req.sessionId,
        "address": f"{client_address}:{req.udpPort}",
        "peerTimeoutSeconds": config.RELAY_PEER_TIMEOUT_SECONDS,
    }


@router.delete("/relay/register")
@router.delete("/gwsc/api/relay/register")
async def unregister_relay_peer(
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    client_address = request.client.host if request.client else None
    removed = 0
    if client_address:
        for peer in relay_registry.peers_for_account(current_user["id"]):
            if peer.address[0] == client_address:
                relay_registry.unregister(peer.address)
                removed += 1
    with closing(get_db()) as conn:
        with conn:
            conn.execute(
                "DELETE FROM relay_peers WHERE account_id = ? AND address = ?",
                (current_user["id"], client_address),
            )
    return {"status": "ok", "removed": removed}


@router.get("/relay/status")
@router.get("/gwsc/api/relay/status")
async def relay_status(current_user: Dict[str, Any] = Depends(get_current_user)):
    peers = [
        {
            "sessionId": peer.session_id,
            "address": f"{peer.address[0]}:{peer.address[1]}",
            "packets": peer.packets,
            "dropped": peer.dropped,
        }
        for peer in relay_registry.peers_for_account(current_user["id"])
    ]
    return {"peers": peers, "registry": relay_registry.stats()}
