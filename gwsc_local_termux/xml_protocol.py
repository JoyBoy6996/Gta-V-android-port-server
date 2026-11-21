"""Safe XML encoding for native ROS-style responses.

The exact XML namespaces and element names used by the Android build have
not been captured.  This module therefore provides deterministic,
well-formed XML without inventing namespaces, and every native route marks
its contract as UNVERIFIED in the documentation.
"""

from typing import Iterable, Optional
from xml.etree import ElementTree as ET

XML_CONTENT_TYPE = "application/xml; charset=utf-8"
XML_DECLARATION = '<?xml version="1.0" encoding="utf-8"?>'


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
