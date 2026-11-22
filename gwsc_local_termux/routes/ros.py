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
import json
import logging
import re
import secrets
import time
import uuid
from contextlib import closing
from typing import Any, Dict, Optional
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Request, Response

from .. import config, native_crypto, presence
from ..auth import authenticate_native_request, get_user_by_token
from ..database import get_db
from ..multiplayer_relay import relay_registry
from ..request_utils import client_ip
from ..security import generate_ticket, hash_token
from ..time_utils import iso_after, is_expired, now_iso
from ..tickets import mint_service_ticket
from ..xml_protocol import (
    XML_CONTENT_TYPE,
    create_ticket_result,
    dynamic_results_xml,
    dynamic_status_xml,
    element,
    error_xml,
    p2p_certificate_xml,
    presence_servers_xml,
    relay_servers_xml,
    response_xml,
    result,
    sc_auth_token_result,
)

log = logging.getLogger("gwsc.ros")
router = APIRouter(tags=["ros"])

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")
_SERVICE_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")
_TITLE_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,32}$")
_VERSION_RE = re.compile(r"^[0-9]{1,5}$")
_SERVICE_SET_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")

# SOURCE_CONFIRMED: privilege 7 is the "banned" flag; a missing credentials
# object makes the native client grant it by default and show ACCESS_DENIED_BANNED.
# 12/20/23-27 are unset in the client's own default table, so they are not
# granted here either.
_GRANTED_PRIVILEGES = tuple(
    value for value in range(28) if value not in (7, 12, 20, 23, 24, 25, 26, 27)
)


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


def _field(values: Dict[str, str], *names: str) -> Optional[str]:
    """Case-insensitive lookup for fields whose native spelling is unverified."""
    for name in names:
        if name in values:
            return values[name]
    lowered = {key.lower(): value for key, value in values.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value is not None:
            return value
    return None


def _session_ticket_header(request: Request) -> Optional[str]:
    """Return the ROS SessionTicket from any observed header spelling.

    LSO uses ``ros-SessionTicket`` for post-auth native calls.  The Android
    client spelling is `UNVERIFIED`, so accept the common variants without
    treating an unrelated header as a credential.
    """
    for name in (
        "ros-sessionticket",
        "ros-session-ticket",
        "x-ros-sessionticket",
    ):
        value = request.headers.get(name)
        if value:
            return value
    return None


def _native_user(
    request: Request,
    values: Dict[str, str],
    *,
    allow_ticket_field: bool = True,
) -> Optional[Dict[str, Any]]:
    form_token = (
        _field(values, "scAuthToken")
        or _field(values, "authToken")
        or _field(values, "token")
    )
    # SOURCE_CONFIRMED: LSO's native matchmaking and P2P handlers authenticate
    # with the ``ticket`` form field.  That field is also the *target* of the
    # legacy renew/invalidate endpoints, so those calls opt out explicitly.
    if allow_ticket_field:
        form_token = form_token or _field(values, "ticket")
    session_ticket = _field(values, "sessionTicket") or _session_ticket_header(request)
    return authenticate_native_request(
        form_token=form_token,
        query_token=request.query_params.get("scAuthToken")
        or request.query_params.get("token"),
        authorization=request.headers.get("authorization"),
        header_token=request.headers.get("x-gwsc-token"),
        session_ticket=session_ticket,
    )


def _require_user(
    request: Request,
    values: Dict[str, str],
    *,
    allow_ticket_field: bool = True,
) -> Optional[Dict[str, Any]]:
    user = _native_user(request, values, allow_ticket_field=allow_ticket_field)
    if not user or user.get("status") != "active":
        return None
    return user


def _native_public_ip(request: Request) -> Optional[str]:
    """Return a valid IPv4 literal for PublicIp, or omit the element."""
    try:
        return str(ipaddress.IPv4Address(client_ip(request)))
    except ValueError:
        return None


def _ticket_xml_response(
    request: Request,
    user: Dict[str, Any],
    *,
    service: str,
    title: str,
    platform: str,
    region: int,
) -> Response:
    """Mint private credentials and return a parser-valid CreateTicket XML.

    ``TESTED``: the XML shape is covered by ``tests/test_native_auth.py``.
    Nothing here claims compatibility with a real Rockstar service; every
    credential is issued by this server, hashed before storage and bound to the
    authenticated account.
    """
    raw_ticket = generate_ticket()
    raw_session_ticket = generate_ticket()
    now = int(time.time())

    with closing(get_db()) as conn:
        mint_service_ticket(
            conn,
            user["id"],
            service=service,
            raw_ticket=raw_ticket,
            title=title,
            platform=platform,
            max_per_service=config.MAX_TICKETS_PER_ACCOUNT,
        )
        mint_service_ticket(
            conn,
            user["id"],
            service=f"{service}.session",
            raw_ticket=raw_session_ticket,
            title=title,
            platform=platform,
            max_per_service=config.MAX_TICKETS_PER_ACCOUNT,
        )

    log.info(
        "Issued native credentials for account=%s service=%s expires=%s",
        user["id"],
        service,
        iso_after(config.TICKET_EXPIRY_SECONDS),
    )
    profile = {
        "rockstar_id": user["rockstar_id"],
        "email": user.get("email", ""),
        "nickname": user.get("nickname", ""),
        "avatar_url": user.get("avatar", ""),
    }
    payload = create_ticket_result(
        ticket=raw_ticket,
        seconds_until_expiration=config.TICKET_EXPIRY_SECONDS,
        region=region,
        player_account_id=user["rockstar_id"],
        session_key=secrets.token_bytes(32),
        session_ticket=raw_session_ticket,
        cloud_key=secrets.token_bytes(32),
        session_id=(now * 1000) & 0x7FFFFFFFFFFFFFFF,
        posix_time=now,
        privileges=_GRANTED_PRIVILEGES,
        public_ip=_native_public_ip(request),
        account=profile,
        # SOURCE_CONFIRMED schema, opt-in via GWSC_NATIVE_SERVICE_HOST.  Empty
        # by default: the launcher's own -rosHost= value remains authoritative.
        service_hosts=config.NATIVE_SERVICE_HOSTS,
    )
    return _xml(response_xml(payload))


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

    return _ticket_xml_response(
        request,
        user,
        service=service,
        title=title,
        platform=platform,
        region=region,
    )


@router.api_route("/{title}/{version}/{service}/CreateTicket{rest:path}", methods=["GET", "POST"])
async def create_ticket_service(request: Request, title: str, version: str, service: str, rest: str = ""):
    return await _create_ticket(request)


@router.api_route("/{title}/{version}/CreateTicket{rest:path}", methods=["GET", "POST"])
async def create_ticket_version(request: Request, title: str, version: str, rest: str = ""):
    return await _create_ticket(request)


@router.api_route("/CreateTicket{rest:path}", methods=["GET", "POST"])
async def create_ticket_root(request: Request, rest: str = ""):
    return await _create_ticket(request)


# ---------------------------------------------------------------------------
# Native .asmx credentials
# ---------------------------------------------------------------------------
# SOURCE_CONFIRMED: rlRosHttpTask::GetServicePath formats
# "%s/%d/%s/%s" (title/version/serviceSet/method) and the create-ticket task's
# service methods are the .asmx names below.  The game posts form params
# ``platformName=pcros&scAuthToken=...`` to these paths.
_NATIVE_TICKET_METHODS = (
    ("auth.asmx", "CreateTicketScAuthToken"),
    ("auth.asmx", "CreateTicketSc3"),
    ("auth.asmx", "CreateTicket"),
    ("socialclub.asmx", "CreateTicketScAuthToken"),
)
_NATIVE_SC_TOKEN_METHODS = (
    ("auth.asmx", "CreateScAuthToken"),
    ("socialclub.asmx", "CreateScAuthToken"),
    ("socialclub.asmx", "GetScAuthToken"),
)


def _native_path_valid(params: Dict[str, str]) -> bool:
    title = params.get("title")
    version = params.get("version")
    service_set = params.get("service_set")
    if title is None:  # unprefixed alias
        return True
    return bool(
        _TITLE_RE.match(title)
        and _VERSION_RE.match(version or "")
        and _SERVICE_SET_RE.match(service_set or "")
    )


def _native_values(
    params: Dict[str, str],
    values: Dict[str, str],
    service: str,
    method: str,
):
    # SOURCE_CONFIRMED: the .asmx service and method are literal path segments
    # from GetServicePath("%s/%d/%s/%s"), not request-supplied values.  They are
    # captured by the per-route wrapper below so an arbitrary path cannot
    # mint credentials for an unexpected service.
    qualified_service = f"{service}/{method}"
    title = params.get("title") or values.get("title") or ""
    platform = values.get("platformName") or values.get("platform") or "android"
    try:
        region = int(values.get("region") or 1)
    except (TypeError, ValueError):
        region = 1
    return qualified_service, title, platform, region


async def native_create_ticket(request: Request, service: str, method: str):
    values = await _request_values(request)
    user = _require_user(request, values)
    if not user:
        return _xml_error(401, 401, "A valid account token is required")
    if not _native_path_valid(request.path_params):
        return _xml_error(400, 400, "Invalid native service path")
    qualified_service, title, platform, region = _native_values(
        request.path_params, values, service, method
    )
    return _ticket_xml_response(
        request,
        user,
        service=qualified_service,
        title=title,
        platform=platform,
        region=region,
    )


async def native_sc_auth_token(request: Request, service: str, method: str):
    values = await _request_values(request)
    user = _require_user(request, values)
    if not user:
        return _xml_error(401, 401, "A valid account token is required")
    if not _native_path_valid(request.path_params):
        return _xml_error(400, 400, "Invalid native service path")
    qualified_service, title, platform, _region = _native_values(
        request.path_params, values, service, method
    )
    raw_token = generate_ticket()
    with closing(get_db()) as conn:
        mint_service_ticket(
            conn,
            user["id"],
            service=qualified_service,
            raw_ticket=raw_token,
            title=title,
            platform=platform,
            max_per_service=3,
        )
    log.info(
        "Issued private SC auth token for account=%s service=%s",
        user["id"],
        qualified_service,
    )
    return _xml(sc_auth_token_result(raw_token))


def _bind_native_endpoint(endpoint, service: str, method: str):
    """Capture the literal .asmx service/method for a registered route.

    FastAPI only injects named path/query parameters into the endpoint
    signature.  A closure keeps these SOURCE_CONFIRMED path constants out of
    client-controlled input while letting both prefixed and root aliases share
    one implementation.
    """

    async def route(request: Request):
        return await endpoint(request, service, method)

    route.__name__ = (
        f"{endpoint.__name__}_{service.replace('.', '_')}_{method}"
    )
    return route


for _service, _method in _NATIVE_TICKET_METHODS:
    _endpoint = _bind_native_endpoint(native_create_ticket, _service, _method)
    router.add_api_route(
        f"/{{title}}/{{version}}/{{service_set}}/{_service}/{_method}",
        _endpoint,
        methods=["GET", "POST"],
        tags=["ros"],
    )
    router.add_api_route(
        f"/{_service}/{_method}",
        _endpoint,
        methods=["GET", "POST"],
        tags=["ros"],
    )

for _service, _method in _NATIVE_SC_TOKEN_METHODS:
    _endpoint = _bind_native_endpoint(native_sc_auth_token, _service, _method)
    router.add_api_route(
        f"/{{title}}/{{version}}/{{service_set}}/{_service}/{_method}",
        _endpoint,
        methods=["GET", "POST"],
        tags=["ros"],
    )
    router.add_api_route(
        f"/{_service}/{_method}",
        _endpoint,
        methods=["GET", "POST"],
        tags=["ros"],
    )


@router.api_route("/ros/ticket/renew", methods=["GET", "POST"])
@router.api_route("/ros.ticket.renew", methods=["GET", "POST"])
@router.api_route("/gwsc/api/ros/ticket/renew", methods=["GET", "POST"])
async def renew_ticket(request: Request):
    values = await _request_values(request)
    user = _require_user(request, values, allow_ticket_field=False)
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

        with conn:
            conn.execute(
                "UPDATE service_tickets SET revoked_at = ? WHERE id = ?",
                (now_iso(), row["id"]),
            )
        service = row["service"]
        title = row["title"] or ""
        platform = row["platform"] or "android"

    return _ticket_xml_response(
        request,
        user,
        service=service,
        title=title,
        platform=platform,
        region=1,
    )


@router.api_route("/ros/ticket/invalidate", methods=["GET", "POST"])
@router.api_route("/ros.ticket.invalidate", methods=["GET", "POST"])
@router.api_route("/gwsc/api/ros/ticket/invalidate", methods=["GET", "POST"])
async def invalidate_ticket(request: Request):
    values = await _request_values(request)
    user = _require_user(request, values, allow_ticket_field=False)
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
# This must be registered before the generic ``...Presence`` aliases below,
# whose path converter would otherwise match ``Presence.asmx/...`` too.
@router.api_route(
    "/{title}/{version}/{service_set}/Presence.asmx/GetPresenceServers",
    methods=["POST"],
)
@router.api_route("/Presence.asmx/GetPresenceServers", methods=["POST"])
async def native_get_presence_servers(request: Request):
    values = await _request_values(request)
    user = _require_user(request, values)
    if not user:
        return _xml_error(401, 401, "A valid account token is required")
    if not _native_path_valid(request.path_params):
        return _xml_error(400, 400, "Invalid native service path")

    try:
        host = native_crypto.validate_relay_host(
            config.RELAY_PUBLIC_ADDRESS, config.RELAY_PUBLIC_PORT
        )
    except native_crypto.NativeCryptoError:
        return _xml_error(503, 503, "Presence service is not configured")
    return _xml(presence_servers_xml(host=host))


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


# ---------------------------------------------------------------------------
# Native matchmaking (.asmx)
# ---------------------------------------------------------------------------
# LSO's advertise/find handlers store the client's opaque ``attrsJson.Data``
# and return it verbatim from Find.  That is the critical contract for making
# a host visible to the game; the server must not try to construct the binary
# netPeerAddress record itself.
_NATIVE_MM_MAX_ATTRS_BYTES = 16 * 384
_NATIVE_MM_MAX_DATA_BYTES = 8 * 384


def _native_platform(request: Request, values: Dict[str, str]) -> str:
    raw = (
        values.get("platformName")
        or values.get("platform")
        or request.headers.get("platform")
        or "android"
    )
    return str(raw).strip().upper()[:32]


def _parse_native_attrs(raw: Optional[str]) -> tuple[dict, str]:
    if raw is None or raw == "":
        return {}, ""
    if len(raw.encode("utf-8")) > _NATIVE_MM_MAX_ATTRS_BYTES:
        raise ValueError("attrsJson is too large")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("attrsJson must be an object")
    data = parsed.get("Data", "")
    if data is None:
        data = ""
    if not isinstance(data, str):
        raise ValueError("attrsJson.Data must be a string")
    if len(data.encode("utf-8")) > _NATIVE_MM_MAX_DATA_BYTES:
        raise ValueError("attrsJson.Data is too large")
    attrs = {str(key): value for key, value in parsed.items() if key != "Data"}
    return attrs, data


def _native_match_row(session) -> ET.Element:
    return element(
        "R",
        attributes={
            "MatchId": session["session_id"],
            "Owner": str(session["host_rockstar_id"]),
        },
        children=[element("Data", session["advertised_data"] or "")],
    )


def _required_slot_count(value: Optional[str], *, allow_zero: bool) -> Optional[int]:
    parsed = _parse_int(value, 0, 0 if allow_zero else 1, config.MAX_SESSION_PLAYERS)
    return parsed


def _close_native_session(conn, session_id: str) -> None:
    now = now_iso()
    with conn:
        conn.execute(
            "UPDATE game_sessions SET is_active = 0, state = 'closed', updated_at = ? "
            "WHERE session_id = ?",
            (now, session_id),
        )
        conn.execute(
            "UPDATE session_members SET status = 'left', last_seen = ? WHERE session_id = ?",
            (now, session_id),
        )
        conn.execute("DELETE FROM relay_peers WHERE session_id = ?", (session_id,))
    relay_registry.unregister_session(session_id)


@router.api_route(
    "/{title}/{version}/{service_set}/matchmaking.asmx/Advertise", methods=["POST"]
)
@router.api_route("/matchmaking.asmx/Advertise", methods=["POST"])
async def native_matchmaking_advertise(request: Request):
    values = await _request_values(request)
    user = _require_user(request, values)
    if not user:
        return _xml_error(401, 401, "A valid account token is required")
    if not _native_path_valid(request.path_params):
        return _xml_error(400, 400, "Invalid native service path")

    num_slots = _required_slot_count(values.get("numSlots"), allow_zero=False)
    available_slots = _required_slot_count(values.get("availableSlots"), allow_zero=True)
    if num_slots is None or available_slots is None:
        return _xml_error(400, 400, "Invalid slot counts")
    if available_slots > num_slots:
        return _xml_error(400, 400, "availableSlots exceeds numSlots")
    try:
        attrs, opaque_data = _parse_native_attrs(values.get("attrsJson"))
    except (ValueError, json.JSONDecodeError):
        return _xml_error(400, 400, "Invalid attrsJson")

    platform = _native_platform(request, values)
    game_version = (
        values.get("gameVersion") or request.headers.get("game-version") or ""
    )[:64]
    host_ip = config.PUBLIC_HOST or client_ip(request)
    host_port = max(1, config.RELAY_PORT)
    match_id = str(uuid.uuid4())
    now = now_iso()
    expires_at = iso_after(config.NATIVE_MATCHMAKING_SESSION_SECONDS)

    with closing(get_db()) as conn:
        active = conn.execute(
            "SELECT session_id FROM game_sessions "
            "WHERE host_account_id = ? AND is_active = 1",
            (user["id"],),
        ).fetchall()
        if len(active) >= config.MAX_SESSIONS_PER_HOST:
            return _xml_error(429, 429, "Too many active sessions")
        for row in active:
            _close_native_session(conn, row["session_id"])

        with conn:
            conn.execute(
                """
                INSERT INTO game_sessions
                    (session_id, host_account_id, host_rockstar_id, host_ip,
                     host_port, session_type, region, visibility, state,
                     max_players, current_players, is_active, attributes,
                     advertised_data, available_slots, platform, game_version,
                     expires_at, created_at, updated_at, last_heartbeat)
                VALUES (?, ?, ?, ?, ?, 'FreeMode', 'unknown', 'public',
                        'advertised', ?, 1, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match_id,
                    user["id"],
                    user["rockstar_id"],
                    host_ip,
                    host_port,
                    num_slots,
                    json.dumps(attrs, separators=(",", ":")),
                    opaque_data,
                    available_slots,
                    platform,
                    game_version,
                    expires_at,
                    now,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO session_members
                    (session_id, account_id, rockstar_id, peer_address, status,
                     joined_at, last_seen)
                VALUES (?, ?, ?, ?, 'joined', ?, ?)
                ON CONFLICT(session_id, rockstar_id) DO UPDATE SET
                    account_id = excluded.account_id,
                    status = 'joined',
                    last_seen = excluded.last_seen
                """,
                (match_id, user["id"], user["rockstar_id"], f"{host_ip}:{host_port}", now, now),
            )

    log.info(
        "Native session advertised match=%s account=%s platform=%s slots=%d/%d data_bytes=%d",
        match_id,
        user["id"],
        platform,
        available_slots,
        num_slots,
        len(opaque_data.encode("utf-8")),
    )
    return _xml(dynamic_status_xml(match_id=match_id))


@router.api_route(
    "/{title}/{version}/{service_set}/matchmaking.asmx/Update", methods=["POST"]
)
@router.api_route("/matchmaking.asmx/Update", methods=["POST"])
async def native_matchmaking_update(request: Request):
    values = await _request_values(request)
    user = _require_user(request, values)
    if not user:
        return _xml_error(401, 401, "A valid account token is required")
    if not _native_path_valid(request.path_params):
        return _xml_error(400, 400, "Invalid native service path")

    match_id = values.get("matchId") or values.get("match_id")
    if not match_id or len(match_id) > 64:
        return _xml_error(400, 400, "Invalid match id")
    num_slots = _required_slot_count(values.get("numSlots"), allow_zero=False)
    available_slots = _required_slot_count(values.get("availableSlots"), allow_zero=True)
    if num_slots is None or available_slots is None or available_slots > num_slots:
        return _xml_error(400, 400, "Invalid slot counts")
    try:
        attrs, opaque_data = _parse_native_attrs(values.get("attrsJson"))
    except (ValueError, json.JSONDecodeError):
        return _xml_error(400, 400, "Invalid attrsJson")

    platform = _native_platform(request, values)
    now = now_iso()
    with closing(get_db()) as conn:
        session = conn.execute(
            "SELECT * FROM game_sessions WHERE session_id = ? AND is_active = 1",
            (match_id,),
        ).fetchone()
        if not session:
            return _xml_error(404, 404, "Session not found")
        member = conn.execute(
            "SELECT id FROM session_members "
            "WHERE session_id = ? AND account_id = ? AND status = 'joined'",
            (match_id, user["id"]),
        ).fetchone()
        if session["host_account_id"] != user["id"] and not member:
            return _xml_error(403, 403, "You are not a member of this session")
        with conn:
            conn.execute(
                """
                UPDATE game_sessions SET
                    max_players = ?, available_slots = ?, attributes = ?,
                    advertised_data = ?, platform = ?, expires_at = ?,
                    updated_at = ?, last_heartbeat = ?
                WHERE session_id = ?
                """,
                (
                    num_slots,
                    available_slots,
                    json.dumps(attrs, separators=(",", ":")),
                    opaque_data,
                    platform,
                    iso_after(config.NATIVE_MATCHMAKING_SESSION_SECONDS),
                    now,
                    now,
                    match_id,
                ),
            )
    return _xml(dynamic_status_xml())


@router.api_route(
    "/{title}/{version}/{service_set}/matchmaking.asmx/Find", methods=["POST"]
)
@router.api_route("/matchmaking.asmx/Find", methods=["POST"])
async def native_matchmaking_find(request: Request):
    values = await _request_values(request)
    user = _require_user(request, values)
    if not user:
        return _xml_error(401, 401, "A valid account token is required")
    if not _native_path_valid(request.path_params):
        return _xml_error(400, 400, "Invalid native service path")

    min_slots = _parse_int(values.get("availableSlots"), 1, 0, config.MAX_SESSION_PLAYERS)
    max_results = _parse_int(values.get("maxResults"), 15, 1, 50)
    if min_slots is None or max_results is None:
        return _xml_error(400, 400, "Invalid matchmaking filters")
    platform = _native_platform(request, values)
    now = now_iso()

    client_is_remote = True
    if request.client:
        try:
            client_is_remote = not ipaddress.ip_address(request.client.host).is_private
        except ValueError:
            client_is_remote = True

    with closing(get_db()) as conn:
        with conn:
            conn.execute(
                "UPDATE game_sessions SET is_active = 0, state = 'closed', updated_at = ? "
                "WHERE expires_at IS NOT NULL AND expires_at <= ? AND is_active = 1",
                (now, now),
            )
        rows = conn.execute(
            """
            SELECT * FROM game_sessions
            WHERE is_active = 1
              AND state IN ('advertised', 'active')
              AND available_slots >= ?
              AND platform IN (?, '')
              AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (min_slots, platform, now, max_results),
        ).fetchall()

    visible = [
        row
        for row in rows
        if not (client_is_remote and _is_unreachable_for_remote(row["host_ip"]))
    ]
    return _xml(dynamic_results_xml([_native_match_row(row) for row in visible]))


@router.api_route(
    "/{title}/{version}/{service_set}/matchmaking.asmx/Unadvertise", methods=["POST"]
)
@router.api_route("/matchmaking.asmx/Unadvertise", methods=["POST"])
async def native_matchmaking_unadvertise(request: Request):
    values = await _request_values(request)
    user = _require_user(request, values)
    if not user:
        return _xml_error(401, 401, "A valid account token is required")
    if not _native_path_valid(request.path_params):
        return _xml_error(400, 400, "Invalid native service path")
    match_id = values.get("matchId") or values.get("match_id")
    if not match_id or len(match_id) > 64:
        return _xml_error(400, 400, "Invalid match id")

    with closing(get_db()) as conn:
        session = conn.execute(
            "SELECT * FROM game_sessions WHERE session_id = ?", (match_id,)
        ).fetchone()
        if not session:
            # LSO treats this as idempotent; do not leak session existence.
            return _xml(dynamic_status_xml(match_id=match_id))
        if session["host_account_id"] != user["id"]:
            return _xml_error(403, 403, "Only the host can unadvertise this session")
        _close_native_session(conn, match_id)
    return _xml(dynamic_status_xml(match_id=match_id))


@router.api_route(
    "/{title}/{version}/{service_set}/matchmaking.asmx/UnadvertiseAll", methods=["POST"]
)
@router.api_route("/matchmaking.asmx/UnadvertiseAll", methods=["POST"])
async def native_matchmaking_unadvertise_all(request: Request):
    values = await _request_values(request)
    user = _require_user(request, values)
    if not user:
        return _xml_error(401, 401, "A valid account token is required")
    if not _native_path_valid(request.path_params):
        return _xml_error(400, 400, "Invalid native service path")

    with closing(get_db()) as conn:
        rows = conn.execute(
            "SELECT session_id FROM game_sessions WHERE host_account_id = ? AND is_active = 1",
            (user["id"],),
        ).fetchall()
        for row in rows:
            _close_native_session(conn, row["session_id"])
    return _xml(dynamic_status_xml())


# ---------------------------------------------------------------------------
# P2P certificates and relay discovery (.asmx)
# ---------------------------------------------------------------------------
@router.api_route(
    "/{title}/{version}/{service_set}/auth.asmx/CreateP2PCertificate",
    methods=["POST"],
)
@router.api_route("/auth.asmx/CreateP2PCertificate", methods=["POST"])
async def native_create_p2p_certificate(request: Request):
    values = await _request_values(request)
    user = _require_user(request, values)
    if not user:
        return _xml_error(401, 401, "A valid account token is required")
    if not _native_path_valid(request.path_params):
        return _xml_error(400, 400, "Invalid native service path")

    public_key = _field(values, "pubKey")
    signature = _field(values, "sig")
    if not public_key or not signature:
        return _xml_error(400, 400, "pubKey and sig are required")
    try:
        native_crypto.validate_signature_field(signature)
        client_cert, ca_cert = native_crypto.issue_p2p_certificate(
            public_key_b64=public_key,
            rockstar_id=user["rockstar_id"],
            nickname=user.get("nickname") or "player",
            ttl_seconds=config.P2P_CERT_TTL_SECONDS,
        )
    except native_crypto.NativeCryptoError as exc:
        message = str(exc)
        if message.startswith(("pubKey", "sig")):
            return _xml_error(400, 400, message)
        log.warning("P2P certificate issuance failed: %s", message)
        return _xml_error(503, 503, "P2P certificate service unavailable")

    return _xml(
        p2p_certificate_xml(
            cert_chain=f"{client_cert.strip()}\n{ca_cert.strip()}",
            seconds_until_expiration=config.P2P_CERT_TTL_SECONDS,
        )
    )


@router.api_route(
    "/{title}/{version}/{service_set}/GeoLocation.asmx/GetRelayServers",
    methods=["POST"],
)
@router.api_route("/GeoLocation.asmx/GetRelayServers", methods=["POST"])
@router.api_route(
    "/{title}/{version}/{service_set}/GeoLocation.asmx/GetLocationInfoFromIP",
    methods=["POST"],
)
@router.api_route("/GeoLocation.asmx/GetLocationInfoFromIP", methods=["POST"])
async def native_get_relay_servers(request: Request):
    values = await _request_values(request)
    user = _require_user(request, values)
    if not user:
        return _xml_error(401, 401, "A valid account token is required")
    if not _native_path_valid(request.path_params):
        return _xml_error(400, 400, "Invalid native service path")

    try:
        host = native_crypto.validate_relay_host(
            config.RELAY_PUBLIC_ADDRESS, config.RELAY_PUBLIC_PORT
        )
    except native_crypto.NativeCryptoError:
        return _xml_error(503, 503, "Relay service is not configured")

    try:
        public_key = native_crypto.relay_public_key_b64()
        signature = native_crypto.sign_relay_host(host)
    except native_crypto.NativeCryptoError as exc:
        log.warning("Relay discovery signing failed: %s", exc)
        return _xml_error(503, 503, "Relay service unavailable")

    return _xml(
        relay_servers_xml(host=host, public_key=public_key, signature=signature)
    )
