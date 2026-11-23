"""Database-backed LSO parity tranche: presence, messages, clans, leaderboards."""

import hashlib
from contextlib import closing
from xml.etree import ElementTree as ET

import pytest

from gwsc_local_termux import database
from gwsc_local_termux.time_utils import now_iso

LSO = "/gta5/11/gameservices"

SOCIAL_ROUTES = (
    ("Presence.asmx", "GetAttributes"),
    ("Presence.asmx", "SetAttributes"),
    ("Presence.asmx", "ReplaceAttributes"),
    ("Presence.asmx", "GetMessages"),
    ("Presence.asmx", "MultiPostMessage"),
    ("Presence.asmx", "Query"),
    ("Presence.asmx", "QueryWithMaxRecordLength"),
    ("Clans.asmx", "GetInvites"),
    ("Clans.asmx", "SetPrimaryClan"),
    ("Clans.asmx", "GetPrimaryClans"),
    ("Clans.asmx", "GetMetadataForClan"),
    ("Clans.asmx", "GetMine"),
    ("Clans.asmx", "GetDesc"),
    ("Leaderboards.asmx", "ReadByCrews"),
)


def _root(response):
    return ET.fromstring(response.text)


def _text(root, name):
    return root.findtext(f".//{{*}}{name}")


def _namespace(root):
    return root.tag[1:].split("}")[0] if root.tag.startswith("{") else ""


def _launch_ticket(client, account):
    response = client.post(
        "/gwsc/api/game-launch",
        headers=account["headers"],
        json={"buildReport": "lso-social"},
    )
    assert response.status_code == 200, response.text
    return response.json()["ticket"]


def _account_id(account):
    with closing(database.get_db()) as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE rockstar_id = ?",
            (account["profile"]["rockstarId"],),
        ).fetchone()
    assert row is not None
    return row["id"]


def _make_account(client, make_account, label):
    suffix = hashlib.sha1(label.encode()).hexdigest()[:8]
    return make_account(email=f"lso-{suffix}@example.invalid", nickname=f"lso{suffix}")


def _fields(ticket, account, service, method):
    values = {"ticket": ticket, "platformName": "pcros"}
    rid = account["profile"]["rockstarId"]
    if method == "GetAttributes":
        values.update({"gamerHandle": f"SC {rid}", "namesCsv": "gsinfo"})
    elif method in ("SetAttributes", "ReplaceAttributes"):
        values["typeNameValueCsv"] = "string,gsinfo,{}"
    elif method == "MultiPostMessage":
        values.update(
            {"recipientsCsv": f'"SC {rid}"', "message": "hello from LSO social"}
        )
    elif method in ("Query", "QueryWithMaxRecordLength"):
        values.update(
            {
                "queryName": "SessionByGamerHandle",
                "paramNameValueCsv": f"@ghandle,SC {rid},ignored",
            }
        )
    elif method == "SetPrimaryClan":
        values["clanId"] = 1
    elif method == "GetDesc":
        values["clanId"] = 1
    return values


def _seed_clan(account, *, primary=False):
    with closing(database.get_db()) as conn:
        with conn:
            now = now_iso()
            clan_id = conn.execute(
                """
                INSERT INTO clans
                    (name, tag, motto, colors, is_system_clan, is_open_clan,
                     is_verified_clan, created_at, updated_at)
                VALUES (?, ?, ?, ?, 0, 1, 0, ?, ?)
                """,
                ("Test Crew", "TEST", "Crew motto", "Blue", now, now),
            ).lastrowid
            rank_id = conn.execute(
                """
                INSERT INTO clan_ranks (clan_id, name, rank_order, system_flags)
                VALUES (?, ?, 0, 9223372036854775807)
                """,
                (clan_id, "Leader"),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO clan_memberships
                    (user_id, clan_id, rank_id, joined_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (_account_id(account), clan_id, rank_id, now, now),
            )
            if primary:
                conn.execute(
                    "UPDATE users SET primary_clan_id = ?, updated_at = ? WHERE id = ?",
                    (clan_id, now, _account_id(account)),
                )
    return clan_id, rank_id


@pytest.mark.parametrize("service,method", SOCIAL_ROUTES)
def test_social_routes_require_native_credentials(client, service, method):
    response = client.post(f"{LSO}/{service}/{method}", data={})
    assert response.status_code == 401
    assert _text(_root(response), "Status") == "0"


@pytest.mark.parametrize("service,method", SOCIAL_ROUTES)
def test_social_routes_return_valid_xml(client, make_account, service, method):
    account = _make_account(client, make_account, f"{service}/{method}")
    if method == "SetPrimaryClan":
        _seed_clan(account)
    response = client.post(
        f"{LSO}/{service}/{method}",
        data=_fields(_launch_ticket(client, account), account, service, method),
    )
    assert response.status_code == 200, response.text
    root = _root(response)
    assert _text(root, "Status") == "1"
    assert root.tag.split("}")[-1] == "Response"


def test_lso_alias_for_new_social_route(client, make_account):
    account = _make_account(client, make_account, "alias/GetAttributes")
    rid = account["profile"]["rockstarId"]
    ticket = _launch_ticket(client, account)
    for path in (
        "/Presence.asmx/GetAttributes",
        "/gta5/11/ros/Presence.asmx/GetAttributes",
    ):
        response = client.post(
            path,
            data={"ticket": ticket, "gamerHandle": f"SC {rid}", "namesCsv": "gsinfo"},
        )
        assert response.status_code == 200, response.text
        assert _text(_root(response), "Status") == "1"


def test_presence_attribute_set_and_get_round_trip(client, make_account):
    account = _make_account(client, make_account, "attributes/roundtrip")
    ticket = _launch_ticket(client, account)
    rid = account["profile"]["rockstarId"]

    response = client.post(
        f"{LSO}/Presence.asmx/SetAttributes",
        data={
            "ticket": ticket,
            "typeNameValueCsv": "string,gsinfo,session-1,string,scName,Goon",
        },
    )
    assert response.status_code == 200, response.text
    assert _text(_root(response), "Status") == "1"
    assert _namespace(_root(response)) == "http://services.ros.rockstargames.com/"

    with closing(database.get_db()) as conn:
        rows = conn.execute(
            "SELECT name, value_type, value_text, sort_order FROM presence_attributes "
            "WHERE account_id = ? ORDER BY sort_order",
            (_account_id(account),),
        ).fetchall()
    assert [(row["name"], row["value_type"], row["value_text"]) for row in rows] == [
        ("gsinfo", "string", "session-1"),
        ("scName", "string", "Goon"),
    ]
    assert rows[0]["sort_order"] < rows[1]["sort_order"]

    response = client.post(
        f"{LSO}/Presence.asmx/GetAttributes",
        data={
            "ticket": ticket,
            "gamerHandle": f"SC {rid}",
            "namesCsv": "gsinfo,scName,missing",
        },
    )
    assert response.status_code == 200, response.text
    root = _root(response)
    assert _text(root, "Count") == "3"
    assert _text(root, "NameValueCsv") == "gsinfo,session-1,scName,Goon,missing,"


def test_presence_attribute_updates_are_account_scoped(client, make_account):
    account = _make_account(client, make_account, "attributes/scoped")
    other = _make_account(client, make_account, "attributes/other")
    ticket = _launch_ticket(client, account)

    response = client.post(
        f"{LSO}/Presence.asmx/SetAttributes",
        data={"ticket": ticket, "typeNameValueCsv": "string,gsinfo,mine"},
    )
    assert response.status_code == 200, response.text

    with closing(database.get_db()) as conn:
        mine = conn.execute(
            "SELECT COUNT(*) FROM presence_attributes WHERE account_id = ?",
            (_account_id(account),),
        ).fetchone()[0]
        theirs = conn.execute(
            "SELECT COUNT(*) FROM presence_attributes WHERE account_id = ?",
            (_account_id(other),),
        ).fetchone()[0]
    assert mine == 1
    assert theirs == 0


@pytest.mark.parametrize(
    "method,csv",
    [
        ("SetAttributes", "string,gsinfo"),
        ("SetAttributes", "string,gsinfo,value,trailing"),
        ("ReplaceAttributes", "a,b,c,d"),
    ],
)
def test_presence_attribute_csv_must_be_triples(client, make_account, method, csv):
    account = _make_account(client, make_account, f"attributes/{method}/{csv}")
    response = client.post(
        f"{LSO}/Presence.asmx/{method}",
        data={"ticket": _launch_ticket(client, account), "typeNameValueCsv": csv},
    )
    assert response.status_code == 400
    assert _text(_root(response), "Status") == "0"


def test_replace_attributes_discards_old_values(client, make_account):
    account = _make_account(client, make_account, "attributes/replace")
    ticket = _launch_ticket(client, account)

    client.post(
        f"{LSO}/Presence.asmx/SetAttributes",
        data={
            "ticket": ticket,
            "typeNameValueCsv": "string,gsinfo,old,string,scName,OldName",
        },
    )
    response = client.post(
        f"{LSO}/Presence.asmx/ReplaceAttributes",
        data={"ticket": ticket, "typeNameValueCsv": "string,scName,NewName"},
    )
    assert response.status_code == 200, response.text

    response = client.post(
        f"{LSO}/Presence.asmx/GetAttributes",
        data={
            "ticket": ticket,
            "gamerHandle": f"SC {account['profile']['rockstarId']}",
            "namesCsv": "gsinfo,scName",
        },
    )
    assert _text(_root(response), "NameValueCsv") == "gsinfo,,scName,NewName"


def test_presence_messages_are_stored_delivered_once(client, make_account):
    sender = _make_account(client, make_account, "messages/sender")
    recipient = _make_account(client, make_account, "messages/recipient")
    sender_id = _account_id(sender)
    recipient_id = _account_id(recipient)
    recipient_rid = recipient["profile"]["rockstarId"]

    response = client.post(
        f"{LSO}/Presence.asmx/MultiPostMessage",
        data={
            "ticket": _launch_ticket(client, sender),
            "recipientsCsv": f'"SC {recipient_rid}"',
            "message": "hello <crew>",
        },
    )
    assert response.status_code == 200, response.text
    root = _root(response)
    assert root.tag == "Response"
    assert _text(root, "Status") == "1"

    with closing(database.get_db()) as conn:
        row = conn.execute(
            "SELECT * FROM social_messages WHERE sender_account_id = ? "
            "AND recipient_account_id = ?",
            (sender_id, recipient_id),
        ).fetchone()
    assert row is not None
    assert row["body"] == "hello <crew>"
    assert row["delivered_at"] is None

    recipient_ticket = _launch_ticket(client, recipient)
    response = client.post(
        f"{LSO}/Presence.asmx/GetMessages", data={"ticket": recipient_ticket}
    )
    assert response.status_code == 200, response.text
    root = _root(response)
    assert _namespace(root) == "GetPresenceServersResponse"
    messages = root.find(".//{*}Messages")
    assert messages.get("Count") == "1"
    assert messages.get("Total") == "1"
    assert messages.get("svc") == "true"
    message = messages.find("{*}message")
    assert message.text == "hello <crew>"
    assert message.get("t").isdigit()
    assert message.get("svc") == "true"

    with closing(database.get_db()) as conn:
        delivered = conn.execute(
            "SELECT delivered_at FROM social_messages WHERE id = ?", (row["id"],)
        ).fetchone()["delivered_at"]
    assert delivered is not None

    response = client.post(
        f"{LSO}/Presence.asmx/GetMessages", data={"ticket": recipient_ticket}
    )
    assert _root(response).find(".//{*}Messages").get("Count") == "0"


@pytest.mark.parametrize(
    "recipients,status",
    [
        ("SC 99999999", 404),
        ("not-a-handle", 400),
        ("", 400),
    ],
)
def test_multi_post_message_rejects_bad_recipients(client, make_account, recipients, status):
    account = _make_account(client, make_account, f"messages/{status}/{recipients}")
    response = client.post(
        f"{LSO}/Presence.asmx/MultiPostMessage",
        data={
            "ticket": _launch_ticket(client, account),
            "recipientsCsv": f'"{recipients}"' if recipients else "",
            "message": "hello",
        },
    )
    assert response.status_code == status
    assert _text(_root(response), "Status") == "0"


def test_presence_query_by_gamer_handle(client, make_account):
    account = _make_account(client, make_account, "query/handle")
    ticket = _launch_ticket(client, account)
    rid = account["profile"]["rockstarId"]
    client.post(
        f"{LSO}/Presence.asmx/SetAttributes",
        data={"ticket": ticket, "typeNameValueCsv": "string,gsinfo,session-42"},
    )

    response = client.post(
        f"{LSO}/Presence.asmx/Query",
        data={
            "ticket": ticket,
            "queryName": "SessionByGamerHandle",
            "paramNameValueCsv": f"@ghandle,SC {rid},ignored",
        },
    )
    assert response.status_code == 200, response.text
    root = _root(response)
    assert _namespace(root) == "PresenceQueryResponse"
    results = root.find(".//{*}Results")
    assert results.get("Count") == "1"
    record = results.find("{*}r")
    assert record.get("gh") == f"SC {rid}"
    assert record.text == '{"gsinfo":"session-42"}'


def test_presence_query_crewmate_sessions_uses_real_active_players(client, make_account):
    account = _make_account(client, make_account, "query/crew")
    ticket = _launch_ticket(client, account)
    client.post(
        f"{LSO}/Presence.asmx/SetAttributes",
        data={"ticket": ticket, "typeNameValueCsv": "string,gsinfo,crew-session"},
    )
    rid = account["profile"]["rockstarId"]
    nickname = account["profile"]["nickname"]

    with closing(database.get_db()) as conn:
        with conn:
            conn.execute(
                "INSERT INTO active_players (rockstar_id, platform, last_seen) "
                "VALUES (?, 'PS4', ?) "
                "ON CONFLICT(rockstar_id, platform) DO UPDATE SET "
                "last_seen = excluded.last_seen",
                (rid, now_iso()),
            )
            conn.execute(
                "INSERT INTO active_players (rockstar_id, platform, last_seen) "
                "VALUES (?, 'PS4', ?)",
                (rid + 1000000, now_iso()),
            )

    response = client.post(
        f"{LSO}/Presence.asmx/QueryWithMaxRecordLength",
        data={
            "ticket": ticket,
            "queryName": "CrewmateSessions",
            "Platform": "PS4",
            "maxRecordLength": "5",
        },
    )
    assert response.status_code == 200, response.text
    results = _root(response).find(".//{*}Results")
    assert results.get("Count") == "1"
    record = results.find("{*}r")
    assert record.get("gh") == f'NP 2 "{nickname}"'
    assert record.text == '{"gsinfo":"crew-session"}'

    with closing(database.get_db()) as conn:
        with conn:
            conn.execute(
                "UPDATE active_players SET last_seen = ? WHERE rockstar_id = ?",
                ("2000-01-01T00:00:00+00:00", rid),
            )
    # An authenticated query refreshes its caller's ActivePlayer row (the same
    # order as upstream's middleware), so observe from a second account that
    # has no session attribute.  The stale first account must be filtered out.
    observer = _make_account(client, make_account, "query/stale-observer")
    response = client.post(
        f"{LSO}/Presence.asmx/Query",
        headers=observer["headers"],
        data={"queryName": "CrewmateSessions", "Platform": "PS4"},
    )
    assert _root(response).find(".//{*}Results").get("Count") == "0"


def test_presence_query_unknown_name_is_error(client, make_account):
    account = _make_account(client, make_account, "query/unknown")
    response = client.post(
        f"{LSO}/Presence.asmx/Query",
        data={"ticket": _launch_ticket(client, account), "queryName": "Unknown"},
    )
    assert response.status_code == 400
    assert _text(_root(response), "Status") == "0"


def test_clans_empty_shapes(client, make_account):
    account = _make_account(client, make_account, "clans/empty")
    ticket = _launch_ticket(client, account)

    response = client.post(f"{LSO}/Clans.asmx/GetInvites", data={"ticket": ticket})
    root = _root(response)
    assert _namespace(root) == "ClanInviteResponse"
    assert root.get("ms") == "10"
    invites = root.find(".//{*}Invites")
    assert invites.get("Count") == "0"
    assert invites.get("MaxCount") == "0"

    for method, container in (
        ("GetMine", "Memberships"),
        ("GetPrimaryClans", "Members"),
    ):
        response = client.post(f"{LSO}/Clans.asmx/{method}", data={"ticket": ticket})
        root = _root(response)
        assert _namespace(root) == "ClanMembershipResponse"
        assert root.find(f".//{{*}}{container}").get("Count") == "0"

    response = client.post(
        f"{LSO}/Clans.asmx/GetMetadataForClan", data={"ticket": ticket}
    )
    root = _root(response)
    assert _namespace(root) == "ClanMetadataEnumResponse"
    assert root.get("ms") == "15"
    metadata = root.find(".//{*}MetadataEnums")
    assert metadata.get("Count") == "0"
    assert metadata.get("MaxCount") == "0"


def test_clans_db_backed_shapes_and_ownership(client, make_account):
    account = _make_account(client, make_account, "clans/member")
    inviter = _make_account(client, make_account, "clans/inviter")
    outsider = _make_account(client, make_account, "clans/outsider")
    clan_id, rank_id = _seed_clan(account, primary=True)

    with closing(database.get_db()) as conn:
        with conn:
            conn.execute(
                """
                INSERT INTO clan_invites
                    (clan_id, inviter_id, invitee_id, message, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (clan_id, _account_id(inviter), _account_id(account), "join us", now_iso()),
            )

    ticket = _launch_ticket(client, account)

    response = client.post(f"{LSO}/Clans.asmx/GetInvites", data={"ticket": ticket})
    invites = _root(response).find(".//{*}Invites")
    assert invites.get("Count") == "1"
    invite = invites.find("{*}Invite")
    assert invite.get("Message") == "join us"
    assert invite.find("{*}Inviter").get("RockstarId") == str(
        inviter["profile"]["rockstarId"]
    )
    assert invite.find("{*}Invitee").get("RockstarId") == str(
        account["profile"]["rockstarId"]
    )
    assert invite.find("{*}Clan").get("Id") == str(clan_id)
    assert invite.find("{*}Clan").get("MemberCount") == "1"
    assert invite.find("{*}Clan").get("IsOpenClan") == "1"

    response = client.post(f"{LSO}/Clans.asmx/GetMine", data={"ticket": ticket})
    memberships = _root(response).find(".//{*}Memberships")
    assert memberships.get("Count") == "1"
    membership = memberships.find("{*}Membership")
    assert membership.get("IsPrimary") == "true"
    assert membership.find("{*}Clan").get("Tag") == "TEST"
    assert membership.find("{*}Rank").get("Id") == str(rank_id)
    assert membership.find("{*}Rank").get("SystemFlags") == "9223372036854775807"

    response = client.post(f"{LSO}/Clans.asmx/GetPrimaryClans", data={"ticket": ticket})
    members = _root(response).find(".//{*}Members")
    assert members.get("Count") == "1"
    assert members.find("{*}Membership").get("IsPrimary") == "true"

    response = client.post(
        f"{LSO}/Clans.asmx/GetDesc", data={"ticket": ticket, "clanId": str(clan_id)}
    )
    clans = _root(response).find(".//{*}Clans")
    assert clans.get("Count") == "1"
    clan = clans.find("{*}Clan")
    assert clan.get("Name") == "Test Crew"
    assert clan.get("Motto") == "Crew motto"
    assert clan.get("IsSystemClan") == "0"
    assert clan.get("IsOpenClan") == "1"

    response = client.post(
        f"{LSO}/Clans.asmx/SetPrimaryClan",
        data={"ticket": ticket, "clanId": str(clan_id)},
    )
    assert response.status_code == 200, response.text
    assert _text(_root(response), "Status") == "1"

    response = client.post(
        f"{LSO}/Clans.asmx/SetPrimaryClan",
        data={"ticket": _launch_ticket(client, outsider), "clanId": str(clan_id)},
    )
    assert response.status_code == 403
    assert _text(_root(response), "Status") == "0"


@pytest.mark.parametrize("method", ["GetDesc", "SetPrimaryClan"])
def test_clans_invalid_clan_id_is_error(client, make_account, method):
    account = _make_account(client, make_account, f"clans/invalid/{method}")
    response = client.post(
        f"{LSO}/Clans.asmx/{method}",
        data={"ticket": _launch_ticket(client, account), "clanId": "12abc"},
    )
    assert response.status_code == 400
    assert _text(_root(response), "Status") == "0"


def test_leaderboard_static_shape(client, make_account):
    account = _make_account(client, make_account, "leaderboard/shape")
    response = client.post(
        f"{LSO}/Leaderboards.asmx/ReadByCrews",
        data={"ticket": _launch_ticket(client, account)},
    )
    assert response.status_code == 200, response.text
    root = _root(response)
    assert root.tag == "Response"
    assert root.attrib == {}
    results = root.find("Results")
    assert results.get("count") == "0"
    assert results.get("total") == "0"
