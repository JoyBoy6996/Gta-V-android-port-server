"""Native matchmaking .asmx endpoints modelled on Los Santos Online.

The opaque ``Data`` value is supplied by the advertising client and must be
returned unchanged.  These tests never invent a binary peer-address layout.
"""

import json
from contextlib import closing
from xml.etree import ElementTree as ET

import pytest

from gwsc_local_termux import database

ADVERTISE = "/gta5/11/gameservices/matchmaking.asmx/Advertise"
UPDATE = "/gta5/11/gameservices/matchmaking.asmx/Update"
FIND = "/gta5/11/gameservices/matchmaking.asmx/Find"
UNADVERTISE = "/gta5/11/gameservices/matchmaking.asmx/Unadvertise"
UNADVERTISE_ALL = "/gta5/11/gameservices/matchmaking.asmx/UnadvertiseAll"


def _root(response):
    return ET.fromstring(response.text)


def _text(root, name):
    node = root.find(f".//{{*}}{name}")
    return node.text if node is not None else None


def _launch_ticket(client, account):
    response = client.post(
        "/gwsc/api/game-launch",
        headers=account["headers"],
        json={"buildReport": "native-matchmaking"},
    )
    assert response.status_code == 200, response.text
    return response.json()["ticket"]


def _native(client, account):
    # LSO's native handlers use the ``ticket`` form field; Android's native
    # spelling is UNVERIFIED, so both are accepted by the auth layer.
    return {"ticket": _launch_ticket(client, account), "platformName": "pcros"}


def _attrs(data, **extra):
    payload = {"Data": data}
    payload.update(extra)
    return json.dumps(payload)


def test_native_advertise_and_find_echo_opaque_data(client, make_account):
    host = make_account(email="mmhost@example.invalid", nickname="mmhost")
    joiner = make_account(email="mmjoiner@example.invalid", nickname="mmjoiner")
    opaque = "QkFTRTY0LU9QQVFVRQ=="

    advertised = client.post(
        ADVERTISE,
        data={
            **_native(client, host),
            "numSlots": "4",
            "availableSlots": "2",
            "attrsJson": _attrs(opaque, MMATTR_REGION="US"),
        },
    )
    assert advertised.status_code == 200, advertised.text
    ad_root = _root(advertised)
    assert _text(ad_root, "Status") == "1"
    match_id = _text(ad_root, "MatchId")
    assert match_id

    found = client.post(
        FIND,
        data={**_native(client, joiner), "availableSlots": "1", "maxResults": "5"},
    )
    assert found.status_code == 200, found.text
    rows = _root(found).findall(".//{*}R")
    assert len(rows) == 1
    assert rows[0].get("MatchId") == match_id
    assert rows[0].get("Owner") == str(host["profile"]["rockstarId"])
    assert _text(rows[0], "Data") == opaque

    with closing(database.get_db()) as conn:
        row = conn.execute(
            "SELECT advertised_data, attributes FROM game_sessions WHERE session_id = ?",
            (match_id,),
        ).fetchone()
    assert row["advertised_data"] == opaque
    assert "Data" not in json.loads(row["attributes"])


def test_native_update_replaces_opaque_data(client, make_account):
    host = make_account(email="mmupdate@example.invalid", nickname="mmupdate")
    native = _native(client, host)
    match_id = _text(
        _root(
            client.post(
                ADVERTISE,
                data={**native, "numSlots": "2", "availableSlots": "1", "attrsJson": _attrs("one")},
            )
        ),
        "MatchId",
    )
    updated = client.post(
        UPDATE,
        data={
            **native,
            "matchId": match_id,
            "numSlots": "3",
            "availableSlots": "1",
            "attrsJson": _attrs("two"),
        },
    )
    assert updated.status_code == 200, updated.text
    assert _text(_root(updated), "Status") == "1"

    found = client.post(FIND, data={**native, "availableSlots": "1"})
    assert _text(_root(found), "Data") == "two"


def test_native_unadvertise_and_unadvertise_all(client, make_account):
    host = make_account(email="mmclose@example.invalid", nickname="mmclose")
    native = _native(client, host)
    match_id = _text(
        _root(
            client.post(
                ADVERTISE,
                data={**native, "numSlots": "2", "availableSlots": "1", "attrsJson": _attrs("close")},
            )
        ),
        "MatchId",
    )

    closed = client.post(UNADVERTISE, data={**native, "matchId": match_id})
    assert closed.status_code == 200, closed.text
    assert _text(_root(closed), "MatchId") == match_id
    assert _root(client.post(FIND, data=native)).findall(".//{*}R") == []

    client.post(
        ADVERTISE,
        data={**native, "numSlots": "2", "availableSlots": "1", "attrsJson": _attrs("again")},
    )
    assert client.post(UNADVERTISE_ALL, data=native).status_code == 200
    assert _root(client.post(FIND, data=native)).findall(".//{*}R") == []


@pytest.mark.parametrize("attrs", ["not-json", json.dumps(["array"])])
def test_native_advertise_rejects_invalid_attrs(client, make_account, attrs):
    account = make_account(email="mmbad@example.invalid", nickname="mmbad")
    response = client.post(
        ADVERTISE,
        data={
            **_native(client, account),
            "numSlots": "2",
            "availableSlots": "1",
            "attrsJson": attrs,
        },
    )
    assert response.status_code == 400
    assert _text(_root(response), "Status") == "0"


def test_native_matchmaking_requires_authentication(client):
    response = client.post(
        FIND, data={"availableSlots": "1", "platformName": "pcros"}
    )
    assert response.status_code == 401
    assert _text(_root(response), "Status") == "0"
