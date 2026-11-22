"""Opt-in native service-host redirect in CreateTicket responses.

The XML schema is SOURCE_CONFIRMED from
``rlRosXmlCreateTicketResponseParser::ParseServiceHosts`` in ``libgtav.so``:
``<Services Count="N"><Hosts><S ep=".." h=".."/></Hosts></Services>``.  These
tests assert the private server emits that shape only when configured and that
values are validated before reaching fixed-size native buffers.
"""

from xml.etree import ElementTree as ET

import pytest

from gwsc_local_termux import config
from gwsc_local_termux.xml_protocol import (
    create_ticket_result,
    response_xml,
    service_hosts,
)

TICKET_PATH = "/gta5/11/gameservices/auth.asmx/CreateTicketScAuthToken"


def _root(response):
    return ET.fromstring(response.text)


def _launch_ticket(client, account):
    response = client.post(
        "/gwsc/api/game-launch",
        headers=account["headers"],
        json={"buildReport": "native-service-hosts"},
    )
    assert response.status_code == 200, response.text
    return response.json()["ticket"]


def _payload(**overrides):
    values = dict(
        ticket="gwsc_ticket_value",
        seconds_until_expiration=3600,
        region=1,
        player_account_id=10001,
        session_key=b"0" * 32,
        session_ticket="gwsc_ticket_session",
        privileges=(3, 5),
    )
    values.update(overrides)
    return create_ticket_result(**values)


def test_service_hosts_empty_when_unset():
    assert service_hosts("") == []
    assert service_hosts("   ") == []
    assert service_hosts(None) == []


def test_service_hosts_builds_expected_pairs():
    assert service_hosts("example.invalid:8443") == [("*", "example.invalid:8443")]
    assert service_hosts("10.0.0.5", "http*://*/gta5/*") == [
        ("http*://*/gta5/*", "10.0.0.5")
    ]


@pytest.mark.parametrize(
    "bad",
    [
        "http://example.invalid",  # scheme is not part of a host
        "example.invalid/path",  # path is not part of a host
        "example.invalid port",  # whitespace
        "ex<ample.invalid",  # XML metacharacter
        "a" * 128,  # native buffer is 127 bytes
    ],
)
def test_service_hosts_rejects_invalid_host(bad):
    with pytest.raises(ValueError):
        service_hosts(bad)


@pytest.mark.parametrize("bad", ["a b", "x" * 128, "ep<thing"])
def test_service_hosts_rejects_invalid_endpoint(bad):
    with pytest.raises(ValueError):
        service_hosts("example.invalid", bad)


def test_create_ticket_xml_omits_services_by_default():
    root = ET.fromstring(response_xml(_payload()))
    assert root.find("./Result/Services") is None


def test_create_ticket_xml_emits_source_confirmed_shape():
    root = ET.fromstring(
        response_xml(_payload(service_hosts=service_hosts("example.invalid:8443")))
    )
    services = root.find("./Result/Services")
    assert services is not None
    assert services.get("Count") == "1"
    host = services.find("./Hosts/S")
    assert host is not None
    assert host.get("ep") == "*"
    assert host.get("h") == "example.invalid:8443"


def test_native_route_uses_configured_service_host(client, make_account, monkeypatch):
    monkeypatch.setattr(
        config, "NATIVE_SERVICE_HOSTS", [("*", "game.example.invalid:8443")]
    )
    account = make_account(email="svchost@example.invalid", nickname="svchost")
    launch_ticket = _launch_ticket(client, account)

    response = client.post(
        TICKET_PATH,
        data={"platformName": "pcros", "scAuthToken": launch_ticket},
    )
    assert response.status_code == 200, response.text
    host = _root(response).find("./Result/Services/Hosts/S")
    assert host is not None
    assert host.get("ep") == "*"
    assert host.get("h") == "game.example.invalid:8443"


def test_native_route_omits_services_without_config(client, make_account, monkeypatch):
    monkeypatch.setattr(config, "NATIVE_SERVICE_HOSTS", [])
    account = make_account(email="nosvchost@example.invalid", nickname="nosvchost")
    launch_ticket = _launch_ticket(client, account)

    response = client.post(
        TICKET_PATH,
        data={"platformName": "pcros", "scAuthToken": launch_ticket},
    )
    assert response.status_code == 200, response.text
    assert _root(response).find("./Result/Services") is None
