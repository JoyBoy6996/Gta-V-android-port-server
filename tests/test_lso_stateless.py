"""Tranche 1 of the LSO parity port: stateless authenticated .asmx services."""

import hashlib
import zlib
from xml.etree import ElementTree as ET

import pytest

LSO = "/gta5/11/gameservices"
ROOT = ""

STATELESS_ROUTES = (
    ("legalpolicies.asmx", "GetAcceptedVersion"),
    ("licenseplates.asmx", "Get"),
    ("licenseplates.asmx", "IsValid"),
    ("CashTransactions.asmx", "GetPackValueUSDE"),
    ("CashTransactions.asmx", "GetLegalTerritoryRestrictions"),
    ("socialclub.asmx", "GetPasswordRequirements"),
    ("socialclub.asmx", "CheckText"),
    ("ugc.asmx", "CheckText"),
    ("achievements.asmx", "Synchronize"),
    ("achievements.asmx", "GetPlayerAchievements"),
    ("telemetry.asmx", "GetTelemetryClientConfig"),
    ("Inbox.asmx", "GetUnreadMessages"),
    ("Inbox.asmx", "PostMessageToRecipients"),
    ("Complaint.asmx", "WriteComplaint"),
    ("entitlements.asmx", "GetSubscriptionDataPSN"),
    ("AgeAssurance.asmx", "PlayerStatus"),
    ("ProfileStats.asmx", "ResetStats"),
    ("Presence.asmx", "Publish"),
    ("Presence.asmx", "PostMessageToRecipients"),
    ("Presence.asmx", "GetAttributesForGamers"),
    ("Presence.asmx", "Subscribe"),
    ("Presence.asmx", "Unsubscribe"),
    ("Presence.asmx", "SignOut"),
)


def _root(response):
    return ET.fromstring(response.text)


def _text(root, name):
    return root.findtext(f".//{{*}}{name}")


def _launch_ticket(client, account):
    response = client.post(
        "/gwsc/api/game-launch",
        headers=account["headers"],
        json={"buildReport": "lso-stateless"},
    )
    assert response.status_code == 200, response.text
    return response.json()["ticket"]


@pytest.mark.parametrize("service,method", STATELESS_ROUTES)
def test_stateless_routes_require_native_credentials(client, service, method):
    response = client.post(f"{LSO}/{service}/{method}", data={})
    assert response.status_code == 401
    assert _text(_root(response), "Status") == "0"


@pytest.mark.parametrize("service,method", STATELESS_ROUTES)
def test_stateless_routes_return_valid_xml(client, make_account, service, method):
    suffix = hashlib.sha1(f"{service}/{method}".encode()).hexdigest()[:6]
    account = make_account(
        email=f"lso-{service}-{method}@example.invalid".lower(),
        nickname=f"lso{suffix}",
    )
    response = client.post(
        f"{LSO}/{service}/{method}",
        data={"ticket": _launch_ticket(client, account), "platformName": "android"},
    )
    assert response.status_code == 200, response.text
    root = _root(response)
    assert _text(root, "Status") == "1"
    assert root.tag.split("}")[-1] == "Response"


@pytest.mark.parametrize(
    "path",
    [
        "/legalpolicies.asmx/GetAcceptedVersion",
        "/gta5/11/gameservices/licenseplates.asmx/Get",
        "/custom/11/ros/licenseplates.asmx/IsValid",
    ],
)
def test_lso_route_aliases(client, make_account, path):
    account = make_account(
        email=f"alias-{path.replace('/', '-')}@example.invalid".lower(),
        nickname=f"alias{abs(hash(path)) % 100000}",
    )
    response = client.post(path, data={"ticket": _launch_ticket(client, account)})
    assert response.status_code == 200, response.text
    assert _text(_root(response), "Status") == "1"


def test_native_generic_path_is_validated(client, make_account):
    account = make_account(email="badpathlso@example.invalid", nickname="badpathlso")
    response = client.post(
        "/bad!/11/ros/legalpolicies.asmx/GetAcceptedVersion",
        data={"ticket": _launch_ticket(client, account)},
    )
    # The generic route matches the segment but rejects unsafe path values.
    assert response.status_code == 400
    assert _text(_root(response), "Status") == "0"


def test_legal_policy_and_license_plate_shapes(client, make_account):
    account = make_account(email="policylso@example.invalid", nickname="policylso")
    ticket = _launch_ticket(client, account)

    response = client.post(
        f"{LSO}/legalpolicies.asmx/GetAcceptedVersion", data={"ticket": ticket}
    )
    root = _root(response)
    assert _text(root, "Version") == "7"
    assert _text(root, "PolicyTag") == "pp"

    response = client.post(f"{LSO}/licenseplates.asmx/Get", data={"ticket": ticket})
    assert _text(_root(response), "PlateText") == "JORBY"

    response = client.post(f"{LSO}/licenseplates.asmx/IsValid", data={"ticket": ticket})
    root = _root(response)
    assert _text(root, "IsValid") == "true"
    assert _text(root, "IsProfane") == "false"
    assert _text(root, "IsReserved") == "false"
    assert _text(root, "IsMalformed") == "false"


def test_cash_catalog_and_restrictions_shapes(client, make_account):
    account = make_account(email="cashlso@example.invalid", nickname="cashlso")
    ticket = _launch_ticket(client, account)

    response = client.post(
        f"{LSO}/CashTransactions.asmx/GetPackValueUSDE", data={"ticket": ticket}
    )
    root = _root(response)
    item_list = root.find(".//{*}ItemList")
    assert item_list is not None
    assert item_list.get("Count") == "29"
    packs = root.findall(".//{*}Pack")
    assert len(packs) == 29
    assert packs[0].get("id") == "12EAA58C2A89418F3C29B4E4BD7A47E6"
    assert packs[0].get("vcValue") == "100000"

    response = client.post(
        f"{LSO}/CashTransactions.asmx/GetLegalTerritoryRestrictions",
        data={"ticket": ticket},
    )
    root = _root(response)
    assert root.find(".//{*}restrictions").get("territoryCode") == "US"
    assert len(root.findall(".//{*}restriction")) == 7


def test_socialclub_and_text_check_shapes(client, make_account):
    account = make_account(email="sociallso@example.invalid", nickname="sociallso")
    ticket = _launch_ticket(client, account)

    response = client.post(
        f"{LSO}/socialclub.asmx/GetPasswordRequirements", data={"ticket": ticket}
    )
    root = _root(response)
    assert _text(root, "ValidSymbols") == "_*;.-!$^"
    assert _text(root, "MinLength") == "8"
    assert _text(root, "MaxLength") == "30"

    for service in ("socialclub.asmx", "ugc.asmx"):
        response = client.post(
            f"{LSO}/{service}/CheckText", data={"ticket": ticket, "text": "anything"}
        )
        assert _text(_root(response), "Count") == "0"


def test_telemetry_config_shape(client, make_account):
    account = make_account(email="telemetrylso@example.invalid", nickname="telemetrylso")
    response = client.post(
        f"{LSO}/telemetry.asmx/GetTelemetryClientConfig",
        data={"ticket": _launch_ticket(client, account)},
    )
    root = _root(response)
    assert root.get("IsTelemetryEnabled") == "true"
    assert root.get("Path") == "telemetryConfig/gta5_ps4_prod_config.json"
    assert root.find(".//{*}RawConfig") is not None


def test_telemetry_submit_validates_raw_deflate(client, make_account):
    account = make_account(email="submitlso@example.invalid", nickname="submitlso")
    ticket = _launch_ticket(client, account)
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    payload = compressor.compress(b"{}") + compressor.flush()
    headers = {
        "content-type": "application/octet-stream",
        "ros-SessionTicket": ticket,
    }

    response = client.post(
        f"{LSO}/telemetry.asmx/SubmitCompressed",
        content=payload,
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert _text(_root(response), "Status") == "1"

    response = client.post(
        f"{LSO}/telemetry.asmx/SubmitCompressed",
        content=b"not raw deflate",
        headers=headers,
    )
    assert response.status_code == 400
    assert _text(_root(response), "Status") == "0"


def test_achievement_inbox_and_misc_shapes(client, make_account):
    account = make_account(email="misclso@example.invalid", nickname="misclso")
    ticket = _launch_ticket(client, account)

    response = client.post(
        f"{LSO}/achievements.asmx/GetPlayerAchievements", data={"ticket": ticket}
    )
    root = _root(response)
    assert root.find(".//{*}AchievementList").get("Count") == "16"
    assert len(root.findall(".//{*}Achievement")) == 16

    response = client.post(
        f"{LSO}/Inbox.asmx/GetUnreadMessages", data={"ticket": ticket}
    )
    messages = _root(response).find(".//{*}Messages")
    assert messages.get("Count") == "0"
    assert messages.get("Total") == "0"

    response = client.post(f"{LSO}/Complaint.asmx/WriteComplaint", data={"ticket": ticket})
    assert _root(response).get("ms") == "200"

    response = client.post(
        f"{LSO}/entitlements.asmx/GetSubscriptionDataPSN", data={"ticket": ticket}
    )
    assert _text(_root(response), "Active") == "true"

    response = client.post(
        f"{LSO}/AgeAssurance.asmx/PlayerStatus", data={"ticket": ticket}
    )
    assert _text(_root(response), "AgeAssuranceStatus") == "NotRequired"

    response = client.post(f"{LSO}/ProfileStats.asmx/ResetStats", data={"ticket": ticket})
    root = _root(response)
    assert _text(root, "Count") == "10"
    assert root.find(".//{*}Results").get("Count") == "10"


def test_healthz_alias(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
