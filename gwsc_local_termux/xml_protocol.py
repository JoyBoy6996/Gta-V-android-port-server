"""Safe XML encoding for native ROS-style responses.

The exact XML namespaces and element names used by the Android build have
not been captured.  This module therefore provides deterministic,
well-formed XML without inventing namespaces, and every native route marks
its contract as UNVERIFIED in the documentation.
"""

import base64
import re
from typing import Iterable, Mapping, Optional
from xml.etree import ElementTree as ET

XML_CONTENT_TYPE = "application/xml; charset=utf-8"
XML_DECLARATION = '<?xml version="1.0" encoding="utf-8"?>'

# SOURCE_CONFIRMED: rlRosXmlCreateTicketResponseParser::ParseServiceHosts reads
# <Services Count="N"><Hosts><S ep="..." h="..."/></Hosts></Services>.  The
# native host buffer is 127 bytes plus a terminator, and StringWildcardCompare
# treats '*' and '?' as wildcards.  Keep both values ASCII-only and short so a
# configured override cannot overflow that buffer or smuggle a scheme/path.
_NATIVE_HOST_RE = re.compile(r"[A-Za-z0-9._\-:\[\]]{1,127}")
_NATIVE_EP_RE = re.compile(r"[A-Za-z0-9._\-:*?/\[\]]{1,127}")


def service_hosts(host: str, endpoint: str = "*") -> list[tuple[str, str]]:
    """Validate an optional native service-host redirect.

    ``SOURCE_CONFIRMED`` schema: each pair is emitted as an ``<S ep=".." h=".."/>``
    child of ``<Services><Hosts>``.  An empty ``host`` returns an empty list so
    callers can pass the result straight to :func:`create_ticket_result` without
    changing the default response.  Invalid values raise instead of being
    silently truncated by the native client.
    """
    host = (host or "").strip()
    endpoint = (endpoint or "*").strip() or "*"
    if not host:
        return []
    if not _NATIVE_HOST_RE.fullmatch(host):
        raise ValueError(
            "native service host must be a bare host[:port] of at most 127 "
            "safe characters (no scheme, path, whitespace or XML metacharacters)"
        )
    if not _NATIVE_EP_RE.fullmatch(endpoint):
        raise ValueError(
            "native service endpoint pattern must be at most 127 safe "
            "characters (wildcards * and ? are allowed)"
        )
    return [(endpoint, host)]


def _element(
    tag: str,
    text: Optional[str] = None,
    *,
    children: Optional[Iterable[ET.Element]] = None,
    attributes: Optional[dict] = None,
) -> ET.Element:
    element = ET.Element(tag, attrib=attributes or {})
    if text is not None:
        element.text = str(text)
    for child in children or []:
        element.append(child)
    return element


def _serialise(root: ET.Element) -> str:
    try:
        ET.indent(root, space="  ")
    except AttributeError:  # pragma: no cover - Python < 3.9 fallback
        pass
    body = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    return f"{XML_DECLARATION}\n{body}\n"


def response_xml(
    result: Optional[ET.Element] = None,
    *,
    status: int = 1,
    children: Optional[Iterable[ET.Element]] = None,
) -> str:
    """Build ``<Response><Status>..</Status><Result>..</Result></Response>``."""
    result_element = result if result is not None else _element("Result")
    if children:
        for child in children:
            result_element.append(child)
    root = _element(
        "Response",
        children=[_element("Status", str(status)), result_element],
    )
    return _serialise(root)


def error_xml(code: int, message: str) -> str:
    root = _element(
        "Response",
        children=[
            _element("Status", "0"),
            _element(
                "Error",
                children=[
                    _element("Code", str(code)),
                    _element("Message", message),
                ],
            ),
        ],
    )
    return _serialise(root)


def element(
    tag: str,
    text: Optional[str] = None,
    *,
    children: Optional[Iterable[ET.Element]] = None,
    attributes: Optional[dict] = None,
) -> ET.Element:
    return _element(tag, text, children=children, attributes=attributes)


def result(children: Iterable[ET.Element]) -> ET.Element:
    return _element("Result", children=list(children))


def ticket_result(
    *,
    ticket: str,
    seconds_until_expiration: int,
    region: int,
    player_account_id: int,
    privileges: int = 0,
    disabled_security_flags: int = 0,
) -> ET.Element:
    return result(
        [
            element("Ticket", ticket),
            element("SecsUntilExpiration", str(seconds_until_expiration)),
            element("Region", str(region)),
            element("PlayerAccountId", str(player_account_id)),
            element("Privileges", str(privileges)),
            element("DisabledSecurityFlags", str(disabled_security_flags)),
        ]
    )


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def create_ticket_result(
    *,
    ticket: str,
    seconds_until_expiration: int,
    region: int,
    player_account_id: int,
    session_key: bytes,
    session_ticket: str,
    privileges: Iterable[int],
    session_id: int = 0,
    cloud_key: Optional[bytes] = None,
    public_ip: Optional[str] = None,
    posix_time: int = 0,
    is_sub_account: bool = False,
    account: Optional[Mapping[str, object]] = None,
    service_hosts: Optional[Iterable[tuple]] = None,
) -> ET.Element:
    """Build the CreateTicket response accepted by the installed client.

    ``SOURCE_CONFIRMED``: the field names, the 32-byte base64 ``SessionKey``,
    the comma-separated ``Privileges`` bit list and the ``RockstarAccount``
    nesting were recovered from the native XML parser and credential helper in
    ``libgtav.so`` (``rlRosXmlCreateTicketResponseParser`` /
    ``rlRosCreateTicketResponseHelper::ApplyCreateTicketResponse``).  The
    *values* are private server credentials, not Rockstar data.

    ``service_hosts`` is an optional iterable of ``(endpoint_pattern, host)``
    pairs emitted as ``<Services Count="N"><Hosts><S ep=".." h=".."/></Hosts>``.
    Build it with :func:`service_hosts` so values are validated before reaching
    the fixed-size native buffers.  Leave empty to omit the block entirely.
    """
    granted = tuple(int(value) for value in privileges)
    children = [
        element("Ticket", ticket),
        element("SecsUntilExpiration", str(seconds_until_expiration)),
        element("Region", str(region)),
        element("PlayerAccountId", str(player_account_id)),
        # The native helper parses this comma-separated list into the
        # credentials privilege bitmask.  Privilege 7 is the "banned" flag and
        # must not be granted; the caller chooses the exact set.
        element("Privileges", ",".join(str(value) for value in granted)),
        element("DisabledSecurityFlags", "0"),
        element("SessionId", str(session_id)),
        element("SessionKey", _b64(session_key)),
        element("SessionTicket", session_ticket),
    ]
    if cloud_key is not None:
        children.append(element("CloudKey", _b64(cloud_key)))
    if public_ip:
        children.append(element("PublicIp", public_ip))
    if posix_time:
        children.append(element("PosixTime", str(posix_time)))
    children.append(element("IsSubAccount", "true" if is_sub_account else "false"))

    profile = dict(account or {})
    children.append(
        element(
            "RockstarAccount",
            children=[
                element("RockstarId", str(profile.get("rockstar_id", player_account_id))),
                element("Age", str(profile.get("age", 21))),
                element("AvatarUrl", str(profile.get("avatar_url", ""))),
                element("CountryCode", str(profile.get("country_code", ""))),
                element("Email", str(profile.get("email", ""))),
                element("LanguageCode", str(profile.get("language_code", ""))),
                element("Nickname", str(profile.get("nickname", ""))),
                element("ZipCode", str(profile.get("zip_code", ""))),
            ],
        )
    )
    # The parser accepts ``<Privs><i id=".." g=".." ed=".."/></Privs>``.  These
    # entries are informational; the bitwise privilege check reads
    # ``Privileges`` above.
    children.append(
        element(
            "Privs",
            children=[
                element(
                    "i",
                    attributes={
                        "id": str(value),
                        "g": "true" if value in granted else "false",
                        "ed": "0",
                    },
                )
                for value in range(28)
            ],
        )
    )

    hosts = list(service_hosts or [])
    if hosts:
        children.append(
            element(
                "Services",
                attributes={"Count": str(len(hosts))},
                children=[
                    element(
                        "Hosts",
                        children=[
                            element("S", attributes={"ep": str(ep), "h": str(host)})
                            for ep, host in hosts
                        ],
                    )
                ],
            )
        )
    return result(children)


def sc_auth_token_result(token: str) -> str:
    """Build the minimal ``<Result>token</Result>`` SC-auth response.

    ``SOURCE_CONFIRMED``: ``rlScCreateScAuthTokenTask::ProcessSuccess`` reads
    the text content of the ``Result`` element into its token buffer.
    """
    return response_xml(element("Result", token))
