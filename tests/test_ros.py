from contextlib import closing
from xml.etree import ElementTree as ET

from gwsc_local_termux import database
from gwsc_local_termux.security import hash_token


def _xml_root(response):
    return ET.fromstring(response.text)


def _ticket_value(response):
    root = _xml_root(response)
    return root.findtext("./Result/Ticket")


def test_create_ticket_requires_authentication(client):
    response = client.post("/CreateTicket")
    assert response.status_code == 401
    assert _xml_root(response).findtext("./Status") == "0"


def test_create_ticket_is_bound_to_authenticated_account(client, make_account):
    first = make_account(email="first@example.invalid", nickname="first")
    second = make_account(email="second@example.invalid", nickname="second")

    response = client.post("/CreateTicket", data={"scAuthToken": second["token"]})
    assert response.status_code == 200
    root = _xml_root(response)
    assert root.findtext("./Status") == "1"
    assert root.findtext("./Result/PlayerAccountId") == str(second["profile"]["rockstarId"])
    assert root.findtext("./Result/PlayerAccountId") != str(first["profile"]["rockstarId"])
    assert root.findtext("./Result/Ticket")

    # No unauthenticated fallback to the latest online user.
    assert client.post("/CreateTicket").status_code == 401


def test_ticket_renew_invalidate_and_ownership(client, make_account):
    owner = make_account(email="owner@example.invalid", nickname="owner")
    other = make_account(email="other@example.invalid", nickname="other")

    created = client.post("/CreateTicket", data={"scAuthToken": owner["token"]})
    ticket = _ticket_value(created)
    assert ticket

    # Another account cannot renew or invalidate it.
    assert client.post(
        "/ros/ticket/renew",
        data={"scAuthToken": other["token"], "ticket": ticket},
    ).status_code == 404
    assert client.post(
        "/ros/ticket/invalidate",
        data={"scAuthToken": other["token"], "ticket": ticket},
    ).status_code == 404

    renewed = client.post(
        "/ros/ticket/renew",
        data={"scAuthToken": owner["token"], "ticket": ticket},
    )
    assert renewed.status_code == 200
    new_ticket = _ticket_value(renewed)
    assert new_ticket and new_ticket != ticket

    invalidated = client.post(
        "/ros/ticket/invalidate",
        data={"scAuthToken": owner["token"], "ticket": new_ticket},
    )
    assert invalidated.status_code == 200
    assert _xml_root(invalidated).findtext("./Result/Invalidated") == "1"

    # Revoked tickets cannot be renewed.
    assert client.post(
        "/ros/ticket/renew",
        data={"scAuthToken": owner["token"], "ticket": new_ticket},
    ).status_code == 403


def test_expired_ticket_cannot_be_renewed(client, make_account):
    account = make_account(email="expired@example.invalid", nickname="expired")
    ticket = _ticket_value(
        client.post("/CreateTicket", data={"scAuthToken": account["token"]})
    )
    with closing(database.get_db()) as conn:
        with conn:
            conn.execute(
                "UPDATE service_tickets SET expires_at = ? WHERE ticket_hash = ?",
                ("2000-01-01T00:00:00+00:00", hash_token(ticket)),
            )
    response = client.post(
        "/ros/ticket/renew",
        data={"scAuthToken": account["token"], "ticket": ticket},
    )
    assert response.status_code == 403
    assert "expired" in _xml_root(response).findtext("./Error/Message").lower()


def test_matchmaking_requires_authentication(client):
    assert client.post("/MatchmakingAdvertiseTask").status_code == 401
    assert client.post("/MatchmakingFindTask").status_code == 401
    assert client.post("/MatchmakingJoinTask").status_code == 401
    assert client.post("/MatchmakingUnadvertiseTask").status_code == 401


def test_matchmaking_advertise_find_join_unadvertise(client, make_account):
    host = make_account(email="host@example.invalid", nickname="host")
    guest = make_account(email="guest@example.invalid", nickname="guest")

    advertised = client.post(
        "/MatchmakingAdvertiseTask",
        data={
            "scAuthToken": host["token"],
            "sessionId": "session-1",
            "port": "6672",
            "maxPlayers": "8",
        },
    )
    assert advertised.status_code == 200
    assert _xml_root(advertised).findtext("./Result/Session/SessionId") == "session-1"

    found = client.post(
        "/MatchmakingFindTask",
        data={"scAuthToken": guest["token"]},
    )
    assert found.status_code == 200
    assert _xml_root(found).findtext("./Result/SessionsCount") == "1"

    joined = client.post(
        "/MatchmakingJoinTask",
        data={"scAuthToken": guest["token"], "sessionId": "session-1"},
    )
    assert joined.status_code == 200
    assert _xml_root(joined).findtext("./Result/Session/SessionId") == "session-1"

    # A non-host cannot unadvertise the session.
    assert client.post(
        "/MatchmakingUnadvertiseTask",
        data={"scAuthToken": guest["token"], "sessionId": "session-1"},
    ).status_code == 403

    unadvertised = client.post(
        "/MatchmakingUnadvertiseTask",
        data={"scAuthToken": host["token"], "sessionId": "session-1"},
    )
    assert unadvertised.status_code == 200
    assert _xml_root(unadvertised).findtext("./Result/Unadvertised") == "1"

    after = client.post("/MatchmakingFindTask", data={"scAuthToken": guest["token"]})
    assert _xml_root(after).findtext("./Result/SessionsCount") == "0"


def test_commit_presence_requires_auth_and_updates_info(client, make_account):
    account = make_account()
    assert client.post("/CommitPresence", data={"state": "in_game"}).status_code == 401

    committed = client.post(
        "/CommitPresence",
        data={"scAuthToken": account["token"], "state": "in_game"},
    )
    assert committed.status_code == 200
    assert _xml_root(committed).findtext("./Result/PresenceCommitted") == "1"
    assert client.get("/gwsc/api/info").json()["online"] == 1

    queried = client.post("/Presence", data={"scAuthToken": account["token"]})
    assert queried.status_code == 200
    assert _xml_root(queried).findtext("./Result/Online") == "1"

    # Committing an explicit offline state must remove presence, not leave a
    # row that still counts as online.
    offline = client.post(
        "/CommitPresence",
        data={"scAuthToken": account["token"], "state": "offline"},
    )
    assert offline.status_code == 200
    assert client.get("/gwsc/api/info").json()["online"] == 0
    queried = client.post("/Presence", data={"scAuthToken": account["token"]})
    assert _xml_root(queried).findtext("./Result/Online") == "0"


def test_unknown_native_route_is_not_a_generic_success(client):
    response = client.post("/unknown/native/task")
    assert response.status_code == 404
