import re
from contextlib import closing

from gwsc_local_termux import config, database
from gwsc_local_termux.security import hash_token
from gwsc_local_termux.time_utils import is_expired, now_iso

LAUNCH_TICKET_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")


def _launch(client, headers, build_report="a" * 64):
    return client.post(
        "/gwsc/api/game-launch",
        headers=headers,
        json={"buildReport": build_report},
    )


def _user_id(email):
    with closing(database.get_db()) as conn:
        row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        return row["id"]


def test_game_launch_requires_authentication(client):
    assert client.post(
        "/gwsc/api/game-launch", json={"buildReport": "x"}
    ).status_code == 401
    assert client.post(
        "/gwsc/api/game-launch",
        headers={"Authorization": "Bearer not-a-token"},
        json={"buildReport": "x"},
    ).status_code == 401


def test_game_launch_rejects_disabled_account(client, make_account):
    account = make_account(email="disabled@example.invalid", nickname="disabled")
    with closing(database.get_db()) as conn:
        with conn:
            conn.execute(
                "UPDATE users SET status = 'disabled' WHERE email = ?",
                ("disabled@example.invalid",),
            )
    assert _launch(client, account["headers"]).status_code == 401


def test_game_launch_validates_body(client, make_account):
    account = make_account(email="body@example.invalid", nickname="body")
    headers = account["headers"]

    assert client.post(
        "/gwsc/api/game-launch", headers=headers, json=["not", "an", "object"]
    ).status_code == 400
    assert client.post(
        "/gwsc/api/game-launch",
        headers={**headers, "Content-Type": "application/json"},
        content=b"",
    ).status_code == 400
    assert client.post(
        "/gwsc/api/game-launch",
        headers={**headers, "Content-Type": "application/json"},
        content=b"{",
    ).status_code == 400
    assert client.post(
        "/gwsc/api/game-launch", headers=headers, json={}
    ).status_code == 400
    assert client.post(
        "/gwsc/api/game-launch", headers=headers, json={"buildReport": ""}
    ).status_code == 400
    assert client.post(
        "/gwsc/api/game-launch", headers=headers, json={"buildReport": 123}
    ).status_code == 400
    assert client.post(
        "/gwsc/api/game-launch",
        headers=headers,
        json={"buildReport": "x" * 513},
    ).status_code == 400


def test_game_launch_returns_ticket_and_stores_only_its_hash(client, make_account):
    account = make_account(email="launch@example.invalid", nickname="launch")
    response = _launch(client, account["headers"])
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"ticket"}
    ticket = payload["ticket"]
    assert LAUNCH_TICKET_RE.match(ticket)
    assert response.headers.get("cache-control") == "no-store"

    with closing(database.get_db()) as conn:
        row = conn.execute(
            "SELECT * FROM service_tickets WHERE ticket_hash = ?",
            (hash_token(ticket),),
        ).fetchone()
        assert row is not None
        assert row["account_id"] == _user_id("launch@example.invalid")
        assert row["service"] == "game-launch"
        assert row["revoked_at"] is None
        assert not is_expired(row["expires_at"])
        assert row["ticket_hash"] != ticket
        # The raw ticket is never stored.
        assert conn.execute(
            "SELECT COUNT(*) FROM service_tickets WHERE ticket_hash = ?",
            (ticket,),
        ).fetchone()[0] == 0


def test_game_launch_unprefixed_alias(client, make_account):
    account = make_account(email="alias@example.invalid", nickname="alias")
    response = client.post(
        "/game-launch",
        headers=account["headers"],
        json={"buildReport": "alias"},
    )
    assert response.status_code == 200
    assert LAUNCH_TICKET_RE.match(response.json()["ticket"])


def test_launch_ticket_authenticates_native_requests_only(client, make_account):
    account = make_account(email="native@example.invalid", nickname="native")
    ticket = _launch(client, account["headers"]).json()["ticket"]

    # The native game presents the launch ticket as scAuthToken.
    presence = client.post("/Presence", data={"scAuthToken": ticket})
    assert presence.status_code == 200, presence.text

    # It is not a launcher REST session.
    assert client.get(
        "/gwsc/api/me", headers={"Authorization": f"Bearer {ticket}"}
    ).status_code == 401


def test_expired_and_revoked_launch_tickets_are_rejected(client, make_account):
    account = make_account(email="expiry@example.invalid", nickname="expiry")

    expired_ticket = _launch(client, account["headers"]).json()["ticket"]
    with closing(database.get_db()) as conn:
        with conn:
            conn.execute(
                "UPDATE service_tickets SET expires_at = ? WHERE ticket_hash = ?",
                ("2000-01-01T00:00:00+00:00", hash_token(expired_ticket)),
            )
    assert client.post(
        "/Presence", data={"scAuthToken": expired_ticket}
    ).status_code == 401

    revoked_ticket = _launch(client, account["headers"]).json()["ticket"]
    with closing(database.get_db()) as conn:
        with conn:
            conn.execute(
                "UPDATE service_tickets SET revoked_at = ? WHERE ticket_hash = ?",
                (now_iso(), hash_token(revoked_ticket)),
            )
    assert client.post(
        "/Presence", data={"scAuthToken": revoked_ticket}
    ).status_code == 401


def test_game_launch_rate_limit(client, make_account, monkeypatch):
    monkeypatch.setattr(config, "GAME_LAUNCH_RATE_LIMIT", 2)
    monkeypatch.setattr(config, "GAME_LAUNCH_RATE_WINDOW", 60)
    account = make_account(email="limit@example.invalid", nickname="limit")

    assert _launch(client, account["headers"]).status_code == 200
    assert _launch(client, account["headers"]).status_code == 200
    limited = _launch(client, account["headers"])
    assert limited.status_code == 429
    assert limited.json()["error"] == "rate_limited"
    assert limited.headers.get("retry-after")


def test_launch_ticket_cap_is_per_service(client, make_account, monkeypatch):
    monkeypatch.setattr(config, "GAME_LAUNCH_RATE_LIMIT", 100)
    monkeypatch.setattr(config, "MAX_LAUNCH_TICKETS_PER_ACCOUNT", 3)
    account = make_account(email="cap@example.invalid", nickname="cap")

    # An unrelated ROS ticket must survive a burst of launches.
    assert client.post(
        "/CreateTicket", data={"scAuthToken": account["token"]}
    ).status_code == 200

    for _ in range(5):
        assert _launch(client, account["headers"]).status_code == 200

    with closing(database.get_db()) as conn:
        launch_count = conn.execute(
            "SELECT COUNT(*) FROM service_tickets WHERE account_id = ? AND service = ?",
            (_user_id("cap@example.invalid"), "game-launch"),
        ).fetchone()[0]
        ros_count = conn.execute(
            "SELECT COUNT(*) FROM service_tickets WHERE account_id = ? AND service = ?",
            (_user_id("cap@example.invalid"), "ros"),
        ).fetchone()[0]
    assert launch_count == 3
    assert ros_count == 1
