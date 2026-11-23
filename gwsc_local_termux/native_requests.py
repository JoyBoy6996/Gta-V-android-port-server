"""Shared request/auth helpers for native ROS-style services.

The Android and platform-native clients post form data, JSON or XML to
``.asmx`` URLs with inconsistent field spellings.  All native parity routers
use these helpers so credential resolution, validation and XML errors stay
centralised.  No helper falls back to an implicit account.
"""

import logging
import re
from typing import Any, Dict, Optional
from xml.etree import ElementTree as ET

from fastapi import Request, Response

from . import config, database
from .auth import authenticate_native_request
from .xml_protocol import XML_CONTENT_TYPE, error_xml

log = logging.getLogger("gwsc.native_requests")

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")
SERVICE_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")
TITLE_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,32}$")
VERSION_RE = re.compile(r"^[0-9]{1,5}$")
SERVICE_SET_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")


def xml_response(content: str, status_code: int = 200) -> Response:
    """Return XML with the project's canonical content type."""
    return Response(content=content, media_type=XML_CONTENT_TYPE, status_code=status_code)


def xml_error(status_code: int, code: int, message: str) -> Response:
    """Return an HTTP error plus a parser-readable XML Status=0 body."""
    return xml_response(error_xml(code, message), status_code=status_code)


async def request_values(request: Request) -> Dict[str, str]:
    """Merge query values with parsed JSON/XML/form values.

    XML DTDs and entity declarations are rejected because native requests do
    not need them and they would allow entity expansion attacks.  Malformed
    bodies become missing fields; the caller returns a protocol error and the
    body is never logged.
    """
    values: Dict[str, str] = {key: value for key, value in request.query_params.items()}
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
        log.info("Could not parse native request body: %s", type(exc).__name__)
    return values


def field(values: Dict[str, str], *names: str) -> Optional[str]:
    """Case-insensitive lookup for fields whose native spelling may vary."""
    for name in names:
        if name in values:
            return values[name]
    lowered = {key.lower(): value for key, value in values.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value is not None:
            return value
    return None


def session_ticket_header(request: Request) -> Optional[str]:
    """Return the ROS SessionTicket from any observed header spelling."""
    for name in (
        "ros-sessionticket",
        "ros-session-ticket",
        "x-ros-sessionticket",
    ):
        value = request.headers.get(name)
        if value:
            return value
    return None


def native_user(
    request: Request,
    values: Dict[str, str],
    *,
    allow_ticket_field: bool = True,
) -> Optional[Dict[str, Any]]:
    """Resolve the authenticated account for a native request."""
    form_token = field(values, "scAuthToken", "authToken", "token")
    # SOURCE_CONFIRMED: LSO's native handlers authenticate with the ``ticket``
    # form field.  Renew/invalidate endpoints opt out because ``ticket`` is the
    # object they operate on rather than a credential.
    if allow_ticket_field:
        form_token = form_token or field(values, "ticket")
    session_ticket = field(values, "sessionTicket") or session_ticket_header(request)
    return authenticate_native_request(
        form_token=form_token,
        query_token=request.query_params.get("scAuthToken")
        or request.query_params.get("token"),
        authorization=request.headers.get("authorization"),
        header_token=request.headers.get("x-gwsc-token"),
        session_ticket=session_ticket,
    )


def _record_native_activity(
    request: Request, values: Dict[str, str], user: Dict[str, Any]
) -> None:
    """Mirror upstream's authenticated ActivePlayer upsert.

    ``ADAPTED``: upstream feeds active-player rows from the decoded ROS
    user-agent inside its encryption middleware.  This project's native
    authentication already exposes a validated platform field/header, so we
    use that instead.  Missing or unknown platforms are simply not recorded;
    a bookkeeping failure must never change the authentication decision.
    """
    platform = field(values, "Platform", "platform") or request.headers.get("platform")
    if not platform:
        return
    conn = None
    try:
        conn = database.get_db()
        with conn:
            database.touch_active_player(conn, user.get("rockstar_id"), platform)
    except Exception:
        log.warning("Could not record native active-player observation")
    finally:
        if conn is not None:
            conn.close()


def require_user(
    request: Request,
    values: Dict[str, str],
    *,
    allow_ticket_field: bool = True,
) -> Optional[Dict[str, Any]]:
    """Resolve an active, authenticated account for a native request."""
    user = native_user(request, values, allow_ticket_field=allow_ticket_field)
    if not user or user.get("status") != "active":
        return None
    _record_native_activity(request, values, user)
    return user


def parse_int(value: Optional[str], default: int, minimum: int, maximum: int) -> Optional[int]:
    """Parse and bound an integer field, returning None on invalid input."""
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        return None
    if parsed < minimum or parsed > maximum:
        return None
    return parsed


def native_path_valid(params: Dict[str, str]) -> bool:
    """Validate captured title/version/serviceSet path parameters."""
    title = params.get("title")
    version = params.get("version")
    service_set = params.get("service_set")
    if title is None:  # unprefixed alias
        return True
    return bool(
        TITLE_RE.match(title)
        and VERSION_RE.match(version or "")
        and SERVICE_SET_RE.match(service_set or "")
    )


def bind_native_endpoint(endpoint, service: str, method: str):
    """Capture literal .asmx service/method values in a registered route."""

    async def route(request: Request):
        return await endpoint(request, service, method)

    route.__name__ = f"{endpoint.__name__}_{service.replace('.', '_')}_{method}"
    return route
