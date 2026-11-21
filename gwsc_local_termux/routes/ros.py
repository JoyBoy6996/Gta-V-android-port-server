"""Native ROS / Social Club-style services.

IMPORTANT: the Android client's native request and response contracts have
not been captured.  Every handler in this module is therefore documented as
UNVERIFIED.  The implementation is deliberately strict:

* no fallback to "the latest online user";
* no fabricated ticket for an unauthenticated caller;
* no generic catch-all success response;
* tickets are bound to the authenticated account and service, stored hashed,
  and checked for expiry/revocation.
"""

import ipaddress
import logging
import re
import secrets
from contextlib import closing
from typing import Any, Dict, Optional
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Request, Response

from .. import config, presence
from ..auth import authenticate_native_request, get_user_by_token
from ..database import get_db
from ..multiplayer_relay import relay_registry
from ..request_utils import client_ip
from ..security import generate_ticket, hash_token
from ..time_utils import iso_after, is_expired, now_iso
from ..xml_protocol import (
    XML_CONTENT_TYPE,
    element,
    error_xml,
    response_xml,
    result,
    ticket_result,
)

log = logging.getLogger("gwsc.ros")
router = APIRouter(tags=["ros"])

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")
_SERVICE_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")


def _xml(content: str, status_code: int = 200) -> Response:
    return Response(content=content, media_type=XML_CONTENT_TYPE, status_code=status_code)


def _xml_error(status_code: int, code: int, message: str) -> Response:
    return _xml(error_xml(code, message), status_code=status_code)


async def _request_values(request: Request) -> Dict[str, str]:
    values: Dict[str, str] = {
        key: value for key, value in request.query_params.items()
    }
    if request.method not in ("POST", "PUT", "PATCH"):
        return values
    content_type = request.headers.get("content-type", "").lower()
    try:
        if "application/json" in content_type:
            payload = await request.json()
            if isinstance(payload, dict):
                values.update({str(k): str(v) for k, v in payload.items() if v is not None})
        elif "xml" in content_type:
            raw = await request.body()
            if len(raw) <= config.MAX_BODY_BYTES:
                # Reject DTD/entity payloads; native requests do not need them.
                lowered = raw.lower()
                if b"<!doctype" in lowered or b"<!entity" in lowered:
                    raise ValueError("XML DTDs are not allowed")
                root = ET.fromstring(raw)
                for node in root.iter():
                    tag = node.tag.split("}")[-1]
                    if node.text and node.text.strip():
                        values.setdefault(tag, node.text.strip())
        else:
            form = await request.form()
            values.update({str(k): str(v) for k, v in form.multi_items()})
    except Exception as exc:
        # Malformed bodies are treated as missing fields; the caller then gets
        # a protocol error rather than a stack trace.  Do not log the body.
        log.info("Could not parse native request body: %s", type(exc).__name__)
    return values


def _native_user(request: Request, values: Dict[str, str]) -> Optional[Dict[str, Any]]:
    return authenticate_native_request(
        form_token=values.get("scAuthToken")
        or values.get("authToken")
        or values.get("token"),
        query_token=request.query_params.get("scAuthToken")
        or request.query_params.get("token"),
        authorization=request.headers.get("authorization"),
        header_token=request.headers.get("x-gwsc-token"),
    )


def _require_user(request: Request, values: Dict[str, str]) -> Optional[Dict[str, Any]]:
    user = _native_user(request, values)
    if not user or user.get("status") != "active":
        return None
    return user


def _session_xml(session) -> ET.Element:
    return element(
        "Session",
        children=[
            element("SessionId", session["session_id"]),
            element("HostGamerId", str(session["host_rockstar_id"])),
            element("HostIp", session["host_ip"]),
            element("HostPort", str(session["host_port"])),
            element("CurrentPlayers", str(session["current_players"])),
            element("MaxPlayers", str(session["max_players"])),
            element("SessionType", session["session_type"] or ""),
            element("Region", session["region"] or ""),
            element("State", session["state"] or ""),
        ],
    )


# ---------------------------------------------------------------------------
# Static configuration service
# ---------------------------------------------------------------------------
@router.get("/RosGamersSettings.json")
@router.post("/RosGamersSettings.json")
async def gamer_settings():
    # UNVERIFIED: shape is a placeholder configuration document, not a
    # claimed Rockstar contract.
    return {"Status": 1, "Result": {"Version": 1, "Settings": {}}}


# ---------------------------------------------------------------------------
# Ticket lifecycle
# ---------------------------------------------------------------------------
async def _create_ticket(request: Request) -> Response:
    values = await _request_values(request)
    user = _require_user(request, values)
    if not user:
        return _xml_error(401, 401, "A valid account token is required")

    service = (
        request.path_params.get("service")
        or values.get("service")
        or values.get("serviceName")
        or "ros"
    )
    if not _SERVICE_RE.match(service):
        return _xml_error(400, 400, "Invalid service name")
    title = request.path_params.get("title") or values.get("title") or ""
    platform = values.get("platformName") or values.get("platform") or "android"
    try:
        region = int(values.get("region") or 1)
    except (TypeError, ValueError):
        region = 1

    raw_ticket = generate_ticket()
    ticket_hash = hash_token(raw_ticket)
    now = now_iso()
    expires_at = iso_after(config.TICKET_EXPIRY_SECONDS)

    with closing(get_db()) as conn:
        with conn:
            conn.execute(
                "DELETE FROM service_tickets WHERE account_id = ? AND expires_at <= ?",
                (user["id"], now),
            )
            rows = conn.execute(
                "SELECT id FROM service_tickets WHERE account_id = ? "
                "ORDER BY issued_at DESC",
                (user["id"],),
            ).fetchall()
            for row in rows[max(0, config.MAX_TICKETS_PER_ACCOUNT - 1):]:
                conn.execute("DELETE FROM service_tickets WHERE id = ?", (row["id"],))
            conn.execute(
                """
                INSERT INTO service_tickets
                    (ticket_hash, account_id, service, title, platform,
                     issued_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticket_hash,
                    user["id"],
                    service,
                    title[:80],
                    platform[:40],
                    now,
                    expires_at,
                ),
            )

    log.info(
        "Issued private ROS ticket for account=%s service=%s expires=%s",
        user["id"],
        service,
        expires_at,
    )
    payload = ticket_result(
        ticket=raw_ticket,
        seconds_until_expiration=config.TICKET_EXPIRY_SECONDS,
        region=region,
        player_account_id=user["rockstar_id"],
    )
    return _xml(response_xml(payload))


@router.api_route("/{title}/{version}/{service}/CreateTicket{rest:path}", methods=["GET", "POST"])
async def create_ticket_service(request: Request, title: str, version: str, service: str, rest: str = ""):
    return await _create_ticket(request)


@router.api_route("/{title}/{version}/CreateTicket{rest:path}", methods=["GET", "POST"])
async def create_ticket_version(request: Request, title: str, version: str, rest: str = ""):
    return await _create_ticket(request)


@router.api_route("/CreateTicket{rest:path}", methods=["GET", "POST"])
async def create_ticket_root(request: Request, rest: str = ""):
    return await _create_ticket(request)


@router.api_route("/ros/ticket/renew", methods=["GET", "POST"])
@router.api_route("/ros.ticket.renew", methods=["GET", "POST"])
@router.api_route("/gwsc/api/ros/ticket/renew", methods=["GET", "POST"])
async def renew_ticket(request: Request):
    values = await _request_values(request)
    user = _require_user(request, values)
    if not user:
        return _xml_error(401, 401, "A valid account token is required")
    raw_ticket = values.get("ticket") or request.headers.get("x-ticket")
    if not raw_ticket:
        return _xml_error(400, 400, "Ticket is required")

    with closing(get_db()) as conn:
        row = conn.execute(
            "SELECT * FROM service_tickets WHERE ticket_hash = ? AND account_id = ?",
            (hash_token(raw_ticket), user["id"]),
        ).fetchone()
        if not row:
            return _xml_error(404, 404, "Ticket not found")
        if row["revoked_at"]:
            return _xml_error(403, 403, "Ticket has been revoked")
        if is_expired(row["expires_at"]):
            return _xml_error(403, 403, "Ticket has expired")

        new_raw = generate_ticket()
        now = now_iso()
        expires_at = iso_after(config.TICKET_EXPIRY_SECONDS)
        with conn:
            conn.execute(
                "UPDATE service_tickets SET revoked_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            conn.execute(
                """
                INSERT INTO service_tickets
                    (ticket_hash, account_id, service, title, platform,
                     issued_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hash_token(new_raw),
                    user["id"],
                    row["service"],
                    row["title"],
                    row["platform"],
                    now,
                    expires_at,
                ),
            )

    payload = ticket_result(
        ticket=new_raw,
        seconds_until_expiration=config.TICKET_EXPIRY_SECONDS,
        region=1,
        player_account_id=user["rockstar_id"],
    )
    return _xml(response_xml(payload))


@router.api_route("/ros/ticket/invalidate", methods=["GET", "POST"])
@router.api_route("/ros.ticket.invalidate", methods=["GET", "POST"])
@router.api_route("/gwsc/api/ros/ticket/invalidate", methods=["GET", "POST"])
async def invalidate_ticket(request: Request):
    values = await _request_values(request)
    user = _require_user(request, values)
    if not user:
        return _xml_error(401, 401, "A valid account token is required")
    raw_ticket = values.get("ticket") or request.headers.get("x-ticket")
    if not raw_ticket:
        return _xml_error(400, 400, "Ticket is required")

    with closing(get_db()) as conn:
        row = conn.execute(
            "SELECT id FROM service_tickets WHERE ticket_hash = ? AND account_id = ?",
            (hash_token(raw_ticket), user["id"]),
        ).fetchone()
        if not row:
            return _xml_error(404, 404, "Ticket not found")
        with conn:
            conn.execute(
                "UPDATE service_tickets SET revoked_at = ? WHERE id = ?",
                (now_iso(), row["id"]),
            )
    return _xml(response_xml(result([element("Invalidated", "1")])))


# ---------------------------------------------------------------------------
# Matchmaking
# ---------------------------------------------------------------------------
def _parse_int(value: Optional[str], default: int, minimum: int, maximum: int) -> Optional[int]:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        return None
    if parsed < minimum or parsed > maximum:
        return None
    return parsed


@router.api_route("/{prefix:path}MatchmakingAdvertiseTask{rest:path}", methods=["POST", "GET"])
@router.api_route("/{title}/{version}/{service}/MatchmakingAdvertise{rest:path}", methods=["POST", "GET"])
async def matchmaking_advertise(request: Request):
    values = await _request_values(request)
    user = _require_user(request, values)
    if not user:
        return _xml_error(401, 401, "A valid account token is required")

    session_id = (
        values.get("sessionId") or values.get("session_id") or secrets.token_hex(16)
    )
    if not _SESSION_ID_RE.match(session_id):
        return _xml_error(400, 400, "Invalid session id")

    port = _parse_int(
        values.get("port") or values.get("hostPort"),
        config.RELAY_PORT,
        1,
        65535,
    )
    if port is None:
        return _xml_error(400, 400, "Invalid host port")
    max_players = _parse_int(
        values.get("maxPlayers"),
        config.MAX_SESSION_PLAYERS,
        1,
        config.MAX_SESSION_PLAYERS,
    )
    if max_players is None:
        return _xml_error(400, 400, "Invalid max players")

    host_ip = config.PUBLIC_HOST or client_ip(request)
    session_type = (values.get("sessionType") or "FreeMode")[:40]
    region = (values.get("region") or "unknown")[:40]
    visibility = (values.get("visibility") or "public").lower()
    if visibility not in ("public", "friends", "private"):
        visibility = "public"

    now = now_iso()
    with closing(get_db()) as conn:
        existing = conn.execute(
            "SELECT * FROM game_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if existing:
            if (
                existing["host_account_id"] not in (None, user["id"])
                and existing["host_rockstar_id"] != user["rockstar_id"]
            ):
                return _xml_error(403, 403, "Session is owned by another account")
            with conn:
                conn.execute(
                    """
                    UPDATE game_sessions SET
                        host_account_id = ?, host_rockstar_id = ?, host_ip = ?,
                        host_port = ?, session_type = ?, region = ?, visibility = ?,
                        state = 'advertised', max_players = ?, is_active = 1,
                        updated_at = ?, last_heartbeat = ?
                    WHERE session_id = ?
                    """,
                    (
                        user["id"],
                        user["rockstar_id"],
                        host_ip,
                        port,
                        session_type,
                        region,
                        visibility,
                        max_players,
                        now,
                        now,
                        session_id,
                    ),
                )
        else:
            active_count = conn.execute(
                "SELECT COUNT(*) FROM game_sessions WHERE host_account_id = ? AND is_active = 1",
                (user["id"],),
            ).fetchone()[0]
            if active_count >= config.MAX_SESSIONS_PER_HOST:
                return _xml_error(429, 429, "Too many active sessions")
            with conn:
                conn.execute(
                    """
                    INSERT INTO game_sessions
                        (session_id, host_account_id, host_rockstar_id, host_ip,
                         host_port, session_type, region, visibility, state,
                         max_players, current_players, is_active, created_at,
                         updated_at, last_heartbeat)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'advertised', ?, 1, 1, ?, ?, ?)
                    """,
                    (
                        session_id,
                        user["id"],
                        user["rockstar_id"],
                        host_ip,
                        port,
                        session_type,
                        region,
                        visibility,
                        max_players,
                        now,
                        now,
                        now,
                    ),
                )

        with conn:
            conn.execute(
                """
                INSERT INTO session_members
                    (session_id, account_id, rockstar_id, peer_address, status,
                     joined_at, last_seen)
                VALUES (?, ?, ?, ?, 'joined', ?, ?)
                ON CONFLICT(session_id, rockstar_id) DO UPDATE SET
                    account_id = excluded.account_id,
                    peer_address = excluded.peer_address,
                    status = 'joined',
                    last_seen = excluded.last_seen
                """,
                (
                    session_id,
                    user["id"],
                    user["rockstar_id"],
                    f"{host_ip}:{port}",
                    now,
                    now,
                ),
            )
        session = conn.execute(
            "SELECT * FROM game_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()

    log.info(
        "Session advertised id=%s host_account=%s address=%s:%d",
        session_id,
        user["id"],
        host_ip,
        port,
    )
    return _xml(response_xml(result([_session_xml(session)])))


def _is_unreachable_for_remote(host_ip: str) -> bool:
    try:
        address = ipaddress.ip_address(host_ip)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


@router.api_route("/{prefix:path}MatchmakingFindTask{rest:path}", methods=["POST", "GET"])
@router.api_route("/{title}/{version}/{service}/MatchmakingFind{rest:path}", methods=["POST", "GET"])
async def matchmaking_find(request: Request):
    values = await _request_values(request)
    user = _require_user(request, values)
    if not user:
        return _xml_error(401, 401, "A valid account token is required")

    limit = _parse_int(
        values.get("limit"), config.MATCHMAKING_RESULT_LIMIT, 1, 50
    ) or config.MATCHMAKING_RESULT_LIMIT
    region = values.get("region")
    session_type = values.get("sessionType")
    cutoff = iso_after(-config.SESSION_TIMEOUT_SECONDS)

    query = """
        SELECT * FROM game_sessions
        WHERE is_active = 1 AND state IN ('advertised', 'active')
          AND COALESCE(last_heartbeat, updated_at, created_at) > ?
    """
    params: list[Any] = [cutoff]
    if region:
        query += " AND region = ?"
        params.append(region)
    if session_type:
        query += " AND session_type = ?"
        params.append(session_type)
    query += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)

    client_is_remote = True
    if request.client:
        try:
            client_is_remote = not ipaddress.ip_address(request.client.host).is_private
        except ValueError:
            client_is_remote = True

    with closing(get_db()) as conn:
        sessions = conn.execute(query, params).fetchall()

    visible = []
    for session in sessions:
        if client_is_remote and _is_unreachable_for_remote(session["host_ip"]):
            # Do not advertise loopback/LAN addresses to a remote client.
            continue
        visible.append(session)

    children = [element("SessionsCount", str(len(visible)))]
    children.extend(_session_xml(session) for session in visible)
    return _xml(response_xml(result(children)))


@router.api_route("/{prefix:path}MatchmakingUnadvertiseTask{rest:path}", methods=["POST", "GET"])
@router.api_route("/{title}/{version}/{service}/MatchmakingUnadvertise{rest:path}", methods=["POST", "GET"])
async def matchmaking_unadvertise(request: Request):
    values = await _request_values(request)
    user = _require_user(request, values)
    if not user:
        return _xml_error(401, 401, "A valid account token is required")
    session_id = values.get("sessionId") or values.get("session_id")
    if not session_id:
        return _xml_error(400, 400, "Session id is required")

    with closing(get_db()) as conn:
        session = conn.execute(
            "SELECT * FROM game_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not session:
            return _xml_error(404, 404, "Session not found")
        if session["host_account_id"] not in (None, user["id"]) and session[
            "host_rockstar_id"
        ] != user["rockstar_id"]:
            return _xml_error(403, 403, "Only the host can unadvertise this session")
        with conn:
            conn.execute(
                "UPDATE game_sessions SET is_active = 0, state = 'closed', updated_at = ? "
                "WHERE session_id = ?",
                (now_iso(), session_id),
            )
            conn.execute(
                "UPDATE session_members SET status = 'left', last_seen = ? WHERE session_id = ?",
                (now_iso(), session_id),
            )
            conn.execute("DELETE FROM relay_peers WHERE session_id = ?", (session_id,))

    relay_registry.unregister_session(session_id)
    return _xml(response_xml(result([element("Unadvertised", "1")])))


@router.api_route("/{prefix:path}MatchmakingJoinTask{rest:path}", methods=["POST", "GET"])
@router.api_route("/{title}/{version}/{service}/MatchmakingJoin{rest:path}", methods=["POST", "GET"])
async def matchmaking_join(request: Request):
    values = await _request_values(request)
    user = _require_user(request, values)
    if not user:
        return _xml_error(401, 401, "A valid account token is required")
    session_id = values.get("sessionId") or values.get("session_id")
    if not session_id:
        return _xml_error(400, 400, "Session id is required")

    now = now_iso()
    with closing(get_db()) as conn:
        session = conn.execute(
            "SELECT * FROM game_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not session or not session["is_active"] or session["state"] == "closed":
            return _xml_error(404, 404, "Session not found")
        if session["visibility"] == "private" and session["host_account_id"] != user["id"]:
            return _xml_error(403, 403, "Session is private")

        existing = conn.execute(
            "SELECT id FROM session_members WHERE session_id = ? AND rockstar_id = ?",
            (session_id, user["rockstar_id"]),
        ).fetchone()
        if not existing:
            current_players = conn.execute(
                "SELECT COUNT(*) FROM session_members WHERE session_id = ? AND status = 'joined'",
                (session_id,),
            ).fetchone()[0]
            if current_players >= session["max_players"]:
                return _xml_error(409, 409, "Session is full")
            with conn:
                conn.execute(
                    """
                    INSERT INTO session_members
                        (session_id, account_id, rockstar_id, peer_address,
                         status, joined_at, last_seen)
                    VALUES (?, ?, ?, NULL, 'joined', ?, ?)
                    """,
                    (session_id, user["id"], user["rockstar_id"], now, now),
                )
                conn.execute(
                    """
                    UPDATE game_sessions SET
                        current_players = (
                            SELECT COUNT(*) FROM session_members
                            WHERE session_id = ? AND status = 'joined'
                        ),
                        updated_at = ?, last_heartbeat = ?
                    WHERE session_id = ?
                    """,
                    (session_id, now, now, session_id),
                )
        session = conn.execute(
            "SELECT * FROM game_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()

    return _xml(response_xml(result([_session_xml(session)])))


# ---------------------------------------------------------------------------
# Presence
# ---------------------------------------------------------------------------
@router.api_route("/{prefix:path}CommitPresence{rest:path}", methods=["POST", "GET"])
async def commit_presence(request: Request):
    values = await _request_values(request)
    user = _require_user(request, values)
    if not user:
        return _xml_error(401, 401, "A valid account token is required")

    state = values.get("state") or "launcher"
    session_id = values.get("sessionId") or values.get("session_id")
    with closing(get_db()) as conn:
        with conn:
            presence.touch(conn, user["id"], state=state, session_id=session_id)
    return _xml(response_xml(result([element("PresenceCommitted", "1")])))


@router.api_route("/{prefix:path}Presence{rest:path}", methods=["POST", "GET"])
async def query_presence(request: Request):
    values = await _request_values(request)
    user = _require_user(request, values)
    if not user:
        return _xml_error(401, 401, "A valid account token is required")

    with closing(get_db()) as conn:
        record = presence.get_presence(conn, user["id"])
    state = record["state"] if record else "offline"
    expires_at = record["expires_at"] if record else ""
    return _xml(
        response_xml(
            result(
                [
                    element("State", state),
                    element("Online", "1" if record else "0"),
                    element("ExpiresAt", expires_at),
                ]
            )
        )
    )
