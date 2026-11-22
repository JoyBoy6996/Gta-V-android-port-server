"""Native .asmx credential endpoints.

The XML field names here are SOURCE_CONFIRMED from libgtav.so's XML parser;
the tests only assert the private server's credentials and their validation.
"""

import base64
from contextlib import closing
from xml.etree import ElementTree as ET

import pytest

from gwsc_local_termux import database
from gwsc_local_termux.security import hash_token

TICKET_PATH = "/gta5/11/gameservices/auth.asmx/CreateTicketScAuthToken"


def _root(response):
    return ET.fromstring(response.text)


def _account_id(email):
    with closing(database.get_db()) as conn:
        return conn.execute(
            "SELECT id FROM users WHERE email = ?", (email,)
        ).fetchone()["id"]


def _launch_ticket(client, account):
    response = client.post(
        "/gwsc/api/game-launch",
        headers=account["headers"],
        json={"buildReport": "native-auth"},
    )
    assert response.status_code == 200, response.text
    return response.json()["ticket"]


def test_native_create_ticket_is_parser_valid_and_bound_to_launch_ticket(client, make_account):
    account = make_account(email="nativeticket@example.invalid", nickname="nativeticket")
    launch_ticket = _launch_ticket(client, account)

    response = client.post(
        TICKET_PATH,
        data={"platformName": "pcros", "scAuthToken": launch_ticket},
    )
    assert response.status_code == 200, response.text
    root = _root(response)
    assert root.findtext("./Status") == "1"

    result = root.find("./Result")
    assert result is not None
    assert (result.findtext("Ticket") or "").startswith("gwsc_ticket_")
    assert int(result.findtext("SecsUntilExpiration")) >= 1
    assert int(result.findtext("PlayerAccountId")) == account["profile"]["rockstarId"]

    key = result.findtext("SessionKey")
    assert key and len(key) == 44
    assert len(base64.b64decode(key, validate=True)) == 32
    assert 0 < len(result.findtext("SessionTicket") or "") <= 127

    privileges = {
        int(value)
        for value in (result.findtext("Privileges") or "").split(",")
        if value
    }
    # Privilege 7 is the client's "banned" flag; it must never be granted.
    assert 7 not in privileges
    assert {3, 5}.issubset(privileges)
    assert result.find("./Privs/i[@id='7']").get("g") == "false"

    sc_account = result.find("./RockstarAccount")
    assert sc_account is not None
    assert int(sc_account.findtext("RockstarId")) == account["profile"]["rockstarId"]
    assert int(sc_account.findtext("Age")) >= 1
    assert sc_account.findtext("Nickname") == "nativeticket"
    assert sc_account.findtext("Email") == "nativeticket@example.invalid"

    with closing(database.get_db()) as conn:
        ticket_hash = hash_token(result.findtext("Ticket"))
        session_hash = hash_token(result.findtext("SessionTicket"))
        assert conn.execute(
            "SELECT COUNT(*) FROM service_tickets WHERE ticket_hash = ? AND service = ?",
            (ticket_hash, "auth.asmx/CreateTicketScAuthToken"),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM service_tickets WHERE ticket_hash = ? AND service = ?",
            (session_hash, "auth.asmx/CreateTicketScAuthToken.session"),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM service_tickets WHERE ticket_hash = ?",
            (ticket_hash,),
        ).fetchone()[0] == 1  # raw value is never stored


@pytest.mark.parametrize(
    "path",
    [
        TICKET_PATH,
        "/gta5/11/gameservices/auth.asmx/CreateTicketSc3",
        "/auth.asmx/CreateTicketScAuthToken",
        "/gta5/11/gameservices/socialclub.asmx/GetScAuthToken",
    ],
)
def test_native_credentials_require_authentication(client, path):
    response = client.post(path, data={"platformName": "pcros"})
    assert response.status_code == 401
    assert _root(response).findtext("./Status") == "0"


def test_native_path_is_validated(client, make_account):
    account = make_account(email="nativepath@example.invalid", nickname="nativepath")
    response = client.post(
        "/bad!/11/gameservices/auth.asmx/CreateTicketScAuthToken",
        data={"scAuthToken": account["token"]},
    )
    assert response.status_code == 400
    assert "Invalid native service path" in _root(response).findtext("./Error/Message")


@pytest.mark.parametrize(
    "path",
    [
        "/auth.asmx/CreateTicketSc3",
        "/socialclub.asmx/CreateTicketScAuthToken",
        "/gta5/11/launcherservices/auth.asmx/CreateTicketScAuthToken",
    ],
)
def test_native_ticket_aliases(client, make_account, path):
    account = make_account(email="aliasnative@example.invalid", nickname="aliasnative")
    response = client.post(path, data={"scAuthToken": account["token"]})
    assert response.status_code == 200, response.text
    assert _root(response).findtext("./Result/Ticket")


@pytest.mark.parametrize(
    "path",
    [
        "/gta5/11/gameservices/auth.asmx/CreateScAuthToken",
        "/gta5/11/gameservices/socialclub.asmx/CreateScAuthToken",
        "/socialclub.asmx/GetScAuthToken",
    ],
)
def test_sc_auth_token_is_real_and_resolvable(client, make_account, path):
    account = make_account(email="sctoken@example.invalid", nickname="sctoken")
    response = client.post(path, data={"scAuthToken": account["token"]})
    assert response.status_code == 200, response.text
    root = _root(response)
    assert root.findtext("./Status") == "1"
    token = root.findtext("./Result")
    assert token and token.startswith("gwsc_ticket_")

    # The token is a real, account-bound service credential.
    assert client.post("/Presence", data={"scAuthToken": token}).status_code == 200
    with closing(database.get_db()) as conn:
        row = conn.execute(
            "SELECT account_id, service FROM service_tickets WHERE ticket_hash = ?",
            (hash_token(token),),
        ).fetchone()
    assert row is not None
    assert row["account_id"] == _account_id(account["profile"]["email"])
    assert row["service"].endswith("/" + path.rsplit("/", 1)[-1])


def test_unknown_native_asmx_method_is_not_a_success(client, make_account):
    account = make_account(email="unknownasmx@example.invalid", nickname="unknownasmx")
    response = client.post(
        "/gta5/11/gameservices/auth.asmx/NotARealMethod",
        data={"scAuthToken": account["token"]},
    )
    assert response.status_code == 404


def test_legacy_create_ticket_also_carries_native_credentials(client, make_account):
    account = make_account(email="legacyfull@example.invalid", nickname="legacyfull")
    response = client.post("/CreateTicket", data={"scAuthToken": account["token"]})
    assert response.status_code == 200, response.text
    result = _root(response).find("./Result")
    assert len(base64.b64decode(result.findtext("SessionKey"), validate=True)) == 32
    assert int(result.findtext("SecsUntilExpiration")) >= 1
    assert "7" not in (result.findtext("Privileges") or "").split(",")
