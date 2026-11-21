import asyncio
from contextlib import closing

from gwsc_local_termux import config, database
from gwsc_local_termux.routes.events import EventBus


def test_friend_request_accept_flow(client, make_account):
    alice = make_account(email="alice@example.invalid", nickname="alice")
    bob = make_account(email="bob@example.invalid", nickname="bob")

    sent = client.post(
        "/gwsc/api/friends", headers=alice["headers"], json={"nickname": "bob"}
    )
    assert sent.status_code == 200

    bob_list = client.get("/gwsc/api/friends", headers=bob["headers"]).json()
    assert [item["nickname"] for item in bob_list["incoming"]] == ["alice"]

    accepted = client.post(
        f"/gwsc/api/friends/{alice['profile']['rockstarId']}/accept",
        headers=bob["headers"],
    )
    assert accepted.status_code == 200

    alice_list = client.get("/gwsc/api/friends", headers=alice["headers"]).json()
    assert [item["nickname"] for item in alice_list["friends"]] == ["bob"]


def test_cannot_accept_without_incoming_request(client, make_account):
    alice = make_account(email="alice@example.invalid", nickname="alice")
    bob = make_account(email="bob@example.invalid", nickname="bob")
    response = client.post(
        f"/gwsc/api/friends/{alice['profile']['rockstarId']}/accept",
        headers=bob["headers"],
    )
    assert response.status_code == 404


def test_cannot_friend_yourself(client, make_account):
    alice = make_account(email="alice@example.invalid", nickname="alice")
    response = client.post(
        "/gwsc/api/friends", headers=alice["headers"], json={"nickname": "alice"}
    )
    assert response.status_code == 400


def test_public_profile_includes_achievements(client, make_account):
    alice = make_account(email="alice@example.invalid", nickname="alice")
    bob = make_account(email="bob@example.invalid", nickname="bob")

    with closing(database.get_db()) as conn:
        user_id = conn.execute(
            "SELECT id FROM users WHERE rockstar_id = ?",
            (alice["profile"]["rockstarId"],),
        ).fetchone()[0]
        with conn:
            conn.execute(
                "INSERT INTO achievements (user_id, achievement_id, unlocked_at) "
                "VALUES (?, ?, ?)",
                (user_id, "first_mission", "2024-01-01T00:00:00+00:00"),
            )

    response = client.get(
        f"/gwsc/api/users/{alice['profile']['rockstarId']}/profile",
        headers=bob["headers"],
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["achievements"]["count"] == 1
    assert payload["achievements"]["recent"][0]["id"] == "first_mission"
    assert payload["game"]["characters"][0]["rank"] >= 1
    # Public profiles must not leak the email address.
    assert "email" not in payload


def test_blocking_hides_profile_and_prevents_messages(client, make_account):
    alice = make_account(email="alice@example.invalid", nickname="alice")
    bob = make_account(email="bob@example.invalid", nickname="bob")

    blocked = client.post(
        f"/gwsc/api/users/{bob['profile']['rockstarId']}/block",
        headers=alice["headers"],
    )
    assert blocked.status_code == 200

    profile = client.get(
        f"/gwsc/api/users/{alice['profile']['rockstarId']}/profile",
        headers=bob["headers"],
    )
    assert profile.status_code == 404

    conversation = client.post(
        "/gwsc/api/conversations",
        headers=bob["headers"],
        json={"rockstarId": alice["profile"]["rockstarId"]},
    )
    assert conversation.status_code == 403


def test_conversation_membership_is_enforced(client, make_account):
    alice = make_account(email="alice@example.invalid", nickname="alice")
    bob = make_account(email="bob@example.invalid", nickname="bob")
    carol = make_account(email="carol@example.invalid", nickname="carol")

    conversation = client.post(
        "/gwsc/api/conversations",
        headers=alice["headers"],
        json={"rockstarId": bob["profile"]["rockstarId"]},
    )
    assert conversation.status_code == 200
    cid = conversation.json()["id"]

    sent = client.post(
        f"/gwsc/api/conversations/{cid}/messages",
        headers=alice["headers"],
        json={"body": "hello bob"},
    )
    assert sent.status_code == 200

    history = client.get(
        f"/gwsc/api/conversations/{cid}/messages", headers=bob["headers"]
    )
    assert history.status_code == 200
    assert history.json()["messages"][0]["body"] == "hello bob"

    # Carol is not a member and must not read or write.
    assert client.get(
        f"/gwsc/api/conversations/{cid}/messages", headers=carol["headers"]
    ).status_code == 403
    assert client.post(
        f"/gwsc/api/conversations/{cid}/messages",
        headers=carol["headers"],
        json={"body": "intrusion"},
    ).status_code == 403


def test_message_validation_and_rate_limit(client, make_account, monkeypatch):
    alice = make_account(email="alice@example.invalid", nickname="alice")
    bob = make_account(email="bob@example.invalid", nickname="bob")
    cid = client.post(
        "/gwsc/api/conversations",
        headers=alice["headers"],
        json={"rockstarId": bob["profile"]["rockstarId"]},
    ).json()["id"]

    empty = client.post(
        f"/gwsc/api/conversations/{cid}/messages",
        headers=alice["headers"],
        json={"body": "   "},
    )
    assert empty.status_code == 400

    monkeypatch.setattr(config, "MAX_MESSAGE_LENGTH", 5)
    too_long = client.post(
        f"/gwsc/api/conversations/{cid}/messages",
        headers=alice["headers"],
        json={"body": "123456"},
    )
    assert too_long.status_code == 400


def test_group_member_removal_authorization(client, make_account):
    alice = make_account(email="alice@example.invalid", nickname="alice")
    bob = make_account(email="bob@example.invalid", nickname="bob")
    carol = make_account(email="carol@example.invalid", nickname="carol")

    group = client.post(
        "/gwsc/api/conversations",
        headers=alice["headers"],
        json={
            "name": "crew",
            "rockstarIds": [
                bob["profile"]["rockstarId"],
                carol["profile"]["rockstarId"],
            ],
        },
    )
    assert group.status_code == 200
    cid = group.json()["id"]

    denied = client.delete(
        f"/gwsc/api/conversations/{cid}/members/{carol['profile']['rockstarId']}",
        headers=bob["headers"],
    )
    assert denied.status_code == 403

    allowed = client.delete(
        f"/gwsc/api/conversations/{cid}/members/{carol['profile']['rockstarId']}",
        headers=alice["headers"],
    )
    assert allowed.status_code == 200


def test_sse_requires_authentication(client, make_account, monkeypatch):
    assert client.get("/gwsc/api/events").status_code == 401
    account = make_account()
    monkeypatch.setattr(config, "SSE_HEARTBEAT_SECONDS", 1)
    with client.stream(
        "GET", "/gwsc/api/events", headers=account["headers"]
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")


def test_event_bus_never_crosses_accounts():
    async def scenario():
        bus = EventBus()
        first = bus.subscribe(1)
        await bus.broadcast("message", {"body": "for two"}, 2)
        assert first.empty()
        await bus.broadcast("message", {"body": "for one"}, 1)
        payload = first.get_nowait()
        assert "for one" in payload
        bus.unsubscribe(first, 1)
        assert bus.connection_count(1) == 0

    asyncio.run(scenario())
