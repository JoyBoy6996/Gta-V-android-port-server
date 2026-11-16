import datetime
import json
import logging
import uuid
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Request, Response
from fastapi.responses import Response as RawResponse, JSONResponse
from ..database import get_db

log = logging.getLogger("gwsc.ros")
router = APIRouter(tags=["ros"])

XML_SUCCESS_HEADER = '<?xml version="1.0" encoding="utf-8"?>\n'

def make_xml_response(result_content: str, status_code: int = 1) -> str:
    return f"""{XML_SUCCESS_HEADER}<Response>
  <Status>{status_code}</Status>
  <Result>
{result_content}
  </Result>
</Response>"""

def make_xml_error(code: int, message: str) -> str:
    return f"""{XML_SUCCESS_HEADER}<Response>
  <Status>0</Status>
  <Error>
    <Code>{code}</Code>
    <Message>{message}</Message>
  </Error>
</Response>"""

@router.get("/RosGamersSettings.json")
@router.post("/RosGamersSettings.json")
async def gamer_settings():
    return {
        "Status": 1,
        "Result": {
            "Version": 1,
            "Settings": {}
        }
    }

@router.api_route("/titles/{path:path}", methods=["GET", "POST"])
async def titles_template(path: str):
    return JSONResponse(content={
        "status": 1,
        "template": {}
    })

# Handle all native CreateTicket endpoints
@router.api_route("/{title}/{version}/{service}/CreateTicket{rest:path}", methods=["GET", "POST"])
@router.api_route("/{title}/{version}/CreateTicket{rest:path}", methods=["GET", "POST"])
@router.api_route("/CreateTicket{rest:path}", methods=["GET", "POST"])
async def handle_create_ticket(request: Request, rest: str = ""):
    # Attempt to extract player account / token from form data or query
    form = {}
    try:
        form = await request.form()
    except Exception:
        pass

    sc_auth_token = form.get("scAuthToken") or request.query_params.get("scAuthToken") or ""
    ticket = form.get("ticket") or request.query_params.get("ticket") or ""
    platform = form.get("platformName") or "pcros"

    conn = get_db()
    cursor = conn.cursor()

    user = None
    if sc_auth_token:
        cursor.execute("""
            SELECT u.* FROM users u
            JOIN tokens t ON u.id = t.user_id
            WHERE t.token = ?
        """, (str(sc_auth_token),))
        user = cursor.fetchone()

    if not user:
        # Fall back to latest active user or default first user
        cursor.execute("SELECT * FROM users ORDER BY is_online DESC, id ASC LIMIT 1")
        user = cursor.fetchone()

    if user:
        rockstar_id = user["rockstar_id"]
        token_str = sc_auth_token or f"gwsc_ticket_{rockstar_id}_{uuid.uuid4().hex[:16]}"
    else:
        rockstar_id = 10001
        token_str = f"gwsc_ticket_default_{uuid.uuid4().hex[:16]}"

    conn.close()

    result_xml = f"""    <Ticket>{token_str}</Ticket>
    <SecsUntilExpiration>86400</SecsUntilExpiration>
    <Region>1</Region>
    <PlayerAccountId>{rockstar_id}</PlayerAccountId>
    <Privileges>0</Privileges>
    <DisabledSecurityFlags>0</DisabledSecurityFlags>"""

    xml_body = make_xml_response(result_xml, status_code=1)
    log.info("ROS CreateTicket issued for Rockstar ID: %d", rockstar_id)
    return RawResponse(content=xml_body, media_type="application/xml")

# Matchmaking Advertise
@router.api_route("/{prefix:path}MatchmakingAdvertiseTask{rest:path}", methods=["POST", "GET"])
@router.api_route("/{title}/{version}/{service}/MatchmakingAdvertise{rest:path}", methods=["POST", "GET"])
async def matchmaking_advertise(request: Request):
    form = {}
    try:
        form = await request.form()
    except Exception:
        pass

    session_id = str(form.get("sessionId") or uuid.uuid4().hex[:16])
    host_ip = request.client.host if request.client else "127.0.0.1"
    host_port = int(form.get("port") or 6672)
    rockstar_id = int(form.get("gamerId") or 10001)

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_db()
    with conn:
        conn.execute("""
            INSERT INTO game_sessions (session_id, host_rockstar_id, host_ip, host_port, session_type, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'FreeMode', 1, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET host_ip = ?, host_port = ?, updated_at = ?, is_active = 1
        """, (session_id, rockstar_id, host_ip, host_port, now, now, host_ip, host_port, now))

        conn.execute("""
            INSERT OR REPLACE INTO session_members (session_id, rockstar_id, peer_address, joined_at)
            VALUES (?, ?, ?, ?)
        """, (session_id, rockstar_id, f"{host_ip}:{host_port}", now))
    conn.close()

    log.info("Matchmaking: Session %s advertised by host %d at %s:%d", session_id, rockstar_id, host_ip, host_port)
    result_xml = f"""    <SessionId>{session_id}</SessionId>
    <HostIp>{host_ip}</HostIp>
    <HostPort>{host_port}</HostPort>"""
    return RawResponse(content=make_xml_response(result_xml), media_type="application/xml")

# Matchmaking Find
@router.api_route("/{prefix:path}MatchmakingFindTask{rest:path}", methods=["POST", "GET"])
@router.api_route("/{title}/{version}/{service}/MatchmakingFind{rest:path}", methods=["POST", "GET"])
async def matchmaking_find(request: Request):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM game_sessions
        WHERE is_active = 1
        ORDER BY updated_at DESC LIMIT 10
    """)
    sessions = cursor.fetchall()
    conn.close()

    items_xml = []
    for s in sessions:
        items_xml.append(f"""    <Session>
      <SessionId>{s["session_id"]}</SessionId>
      <HostGamerId>{s["host_rockstar_id"]}</HostGamerId>
      <HostIp>{s["host_ip"]}</HostIp>
      <HostPort>{s["host_port"]}</HostPort>
      <CurrentPlayers>{s["current_players"]}</CurrentPlayers>
      <MaxPlayers>{s["max_players"]}</MaxPlayers>
    </Session>""")

    result_xml = "\n".join(items_xml) if items_xml else "    <SessionsCount>0</SessionsCount>"
    return RawResponse(content=make_xml_response(result_xml), media_type="application/xml")

# Matchmaking Unadvertise
@router.api_route("/{prefix:path}MatchmakingUnadvertiseTask{rest:path}", methods=["POST", "GET"])
@router.api_route("/{title}/{version}/{service}/MatchmakingUnadvertise{rest:path}", methods=["POST", "GET"])
async def matchmaking_unadvertise(request: Request):
    form = {}
    try:
        form = await request.form()
    except Exception:
        pass
    sid = form.get("sessionId")
    if sid:
        conn = get_db()
        with conn:
            conn.execute("UPDATE game_sessions SET is_active = 0 WHERE session_id = ?", (str(sid),))
        conn.close()
    return RawResponse(content=make_xml_response("<Unadvertised>1</Unadvertised>"), media_type="application/xml")

# Presence Commit / Query
@router.api_route("/{prefix:path}CommitPresence{rest:path}", methods=["POST", "GET"])
@router.api_route("/{prefix:path}Presence{rest:path}", methods=["POST", "GET"])
async def handle_presence(request: Request):
    return RawResponse(content=make_xml_response("<PresenceCommitted>1</PresenceCommitted>"), media_type="application/xml")

# Catch-all for other native ROS requests
@router.api_route("/{title}/{version}/{service}/{method:path}", methods=["GET", "POST", "PUT"])
async def handle_generic_ros(title: str, version: str, service: str, method: str, request: Request):
    log.info("ROS generic request: /%s/%s/%s/%s", title, version, service, method)
    return RawResponse(content=make_xml_response("<Success>1</Success>"), media_type="application/xml")
