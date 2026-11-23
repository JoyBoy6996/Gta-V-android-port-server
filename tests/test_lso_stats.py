"""LSO ProfileStats parity tranche tests.

``TESTED``: authenticated, account-scoped reads and writes backed by imported
lookup metadata and real stored values.  No zero-fill, no fabricated timestamp,
no client-selected account.  Android acceptance remains ``UNVERIFIED``.
"""

import base64
import json
import struct
import zlib
from contextlib import closing
from xml.etree import ElementTree as ET

import pytest

from gwsc_local_termux import config, database
from gwsc_local_termux.profile_stats_codec import (
    TYPE_FLOAT,
    TYPE_INT32,
    TYPE_INT64,
    StatValue,
    parse_stats_blob,
)
from gwsc_local_termux.profile_stats_store import read_stats, stats_key, write_stats

LSO = "/gta5/11/gameservices/ProfileStats.asmx"
NATIVE = "/gta5/11/ros/ProfileStats.asmx"
ROOT = "/ProfileStats.asmx"
METHODS = ("ReadStatsByGamer2", "ReadStatsByGroups", "WriteStats", "ResetStats")


def _ticket(client, account):
    response = client.post(
        "/gwsc/api/game-launch",
        headers=account["headers"],
        json={"buildReport": "lso-stats"},
    )
    assert response.status_code == 200, response.text
    return response.json()["ticket"]


def _user(account):
    with closing(database.get_db()) as conn:
        row = conn.execute(
            "SELECT id, rockstar_id, nickname, status FROM users WHERE rockstar_id = ?",
            (account["profile"]["rockstarId"],),
        ).fetchone()
    assert row is not None
    return dict(row)


def _post(client, method, values, prefix=LSO):
    return client.post(f"{prefix}/{method}", data=values)


def _root(response):
    return ET.fromstring(response.text)


def _text(root, name):
    return root.findtext(f"./{{*}}{name}")


def _ns(root):
    return root.tag[1:].split("}")[0] if root.tag.startswith("{") else ""


def _seed_lookup(
    stat_id,
    *,
    table="stat_lookups",
    stat_type=1,
    group=1,
    name=None,
    raw_type="int",
    server_authoritative=False,
):
    with closing(database.get_db()) as conn:
        with conn:
            conn.execute(
                f"INSERT OR REPLACE INTO {table} "
                "(hash, name, type, raw_type, category, group_name, flags) "
                "VALUES (?, ?, ?, ?, '0', ?, ?)",
                (
                    str(stat_id),
                    name or f"STAT_{stat_id}",
                    str(stat_type),
                    raw_type,
                    str(group),
                    json.dumps({"ServerAuthoritative": server_authoritative}),
                ),
            )


def _seed_blob(account, platform, stats):
    write_stats(_user(account), platform, stats)


def _b64_ids(ids, *, signed=False):
    fmt = ">i" if signed else ">I"
    return base64.b64encode(b"".join(struct.pack(fmt, value) for value in ids)).decode()


def _packed(stats):
    groups = {
        TYPE_INT64: [stat for stat in stats if stat.type == TYPE_INT64],
        TYPE_INT32: [stat for stat in stats if stat.type == TYPE_INT32],
        TYPE_FLOAT: [stat for stat in stats if stat.type == TYPE_FLOAT],
    }
    payload = struct.pack(
        ">hhh",
        len(groups[TYPE_INT64]),
        len(groups[TYPE_INT32]),
        len(groups[TYPE_FLOAT]),
    )
    for stat in groups[TYPE_INT64]:
        payload += struct.pack(">Iq", stat.stat_id, stat.value)
    for stat in groups[TYPE_INT32]:
        payload += struct.pack(">Ii", stat.stat_id, stat.value)
    for stat in groups[TYPE_FLOAT]:
        payload += struct.pack(">If", stat.stat_id, stat.value)
    return payload


def _write_body(ticket, packed, *, platform="PS4", prefix=b"\x09"):
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    compressed = prefix + compressor.compress(packed) + compressor.flush()
    return (
        f"/?ticket={ticket}&Platform={platform}".encode(),
        b"data=" + compressed,
    )


def _post_write(client, ticket, packed, **kwargs):
    query, body = _write_body(ticket, packed, **kwargs)
    return client.post(
        f"{LSO}/WriteStats{query.decode()}",
        content=body,
        headers={"Content-Type": "text/plain"},
    )


# ---------------------------------------------------------------------------
# Authentication, aliases and pinned ResetStats XML
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("method", METHODS)
def test_all_stats_methods_require_authentication(client, method):
    response = _post(client, method, {"Platform": "PS4"})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "method,extra",
    [
        ("ReadStatsByGamer2", {"gamerHandles": "NP 2 anyone", "statIds": ""}),
        ("ReadStatsByGroups", {"groupIds": ""}),
        ("ResetStats", {}),
    ],
)
def test_native_and_root_aliases_reach_the_same_handlers(client, make_account, method, extra):
    account = make_account(
        email=f"stats-alias-{method}@example.invalid", nickname=f"alias{method}"
    )
    ticket = _ticket(client, account)
    for prefix in (LSO, NATIVE, ROOT):
        response = _post(client, method, {"ticket": ticket, "Platform": "PS4", **extra}, prefix)
        assert response.status_code == 200, (prefix, response.text)


def test_write_stats_aliases_reach_the_same_handler(client, make_account):
    account = make_account(email="stats-writealias@example.invalid", nickname="writealias")
    ticket = _ticket(client, account)
    query, body = _write_body(ticket, _packed([]))
    for prefix in (LSO, NATIVE, ROOT):
        response = client.post(
            f"{prefix}/WriteStats?{query.decode().lstrip('/?')}",
            content=body,
            headers={"Content-Type": "text/plain"},
        )
        assert response.status_code == 200, (prefix, response.text)
        assert _text(_root(response), "NumWritten") == "0"


def test_reset_stats_xml_is_pinned(client, make_account):
    account = make_account(email="stats-reset@example.invalid", nickname="statsreset")
    response = _post(
        client, "ResetStats", {"ticket": _ticket(client, account), "Platform": "PS4"}
    )
    assert response.status_code == 200, response.text
    root = _root(response)
    assert _ns(root) == "ResetStatsResponse"
    assert _text(root, "Status") == "1"
    assert _text(root, "Count") == "10"
    assert root.find(".//{*}Results").get("count") == "10"
    assert root.find(".//{*}Results").get("Count") is None


@pytest.mark.parametrize("platform", ["", "PS2", "switch", "../stats"])
def test_read_stats_rejects_unsupported_platform(client, make_account, platform):
    account = make_account(email=f"stats-platform-{platform}@example.invalid", nickname=f"plat{abs(hash(platform))}")
    response = _post(
        client,
        "ReadStatsByGroups",
        {"ticket": _ticket(client, account), "Platform": platform, "groupIds": ""},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# ReadStatsByGroups
# ---------------------------------------------------------------------------
def test_read_groups_returns_only_stored_seeded_values(client, make_account):
    account = make_account(email="stats-groups@example.invalid", nickname="statsgroups")
    ticket = _ticket(client, account)
    _seed_lookup(100, stat_type=1, group=7)
    _seed_lookup(200, stat_type=2, group=7, raw_type="float")
    _seed_lookup(300, stat_type=26, group=8, raw_type="s64")
    _seed_blob(account, "PS4", {100: StatValue(100, TYPE_INT32, 5), 200: StatValue(200, TYPE_FLOAT, 1.5)})

    response = _post(
        client,
        "ReadStatsByGroups",
        {"ticket": ticket, "Platform": "PS4", "groupIds": _b64_ids([7])},
    )
    assert response.status_code == 200, response.text
    root = _root(response)
    assert _ns(root) == "ReadStatsByGroupsResponse"
    assert _text(root, "Status") == "1"
    values = parse_stats_blob(base64.b64decode(_text(root, "Values"), validate=True))
    assert set(values) == {100, 200}
    assert values[100].value == 5
    assert values[200].value == 1.5
    # No zero-fill for the seeded-but-unstored id 300.
    assert 300 not in values


def test_read_groups_on_empty_store_has_no_fabricated_state(client, make_account):
    account = make_account(email="stats-empty@example.invalid", nickname="statsempty")
    _seed_lookup(100, group=7)
    response = _post(
        client,
        "ReadStatsByGroups",
        {
            "ticket": _ticket(client, account),
            "Platform": "PS4",
            "groupIds": _b64_ids([7]),
        },
    )
    assert response.status_code == 200, response.text
    assert _text(_root(response), "Values") == ""


def test_ps5_uses_gen9_lookup_table(client, make_account):
    account = make_account(email="stats-gen9@example.invalid", nickname="statsgen9")
    ticket = _ticket(client, account)
    _seed_lookup(400, stat_type=1, group=3)
    _seed_lookup(400, stat_type=2, group=3, table="gen9_stat_lookups", raw_type="float")
    _seed_blob(account, "PS5", {400: StatValue(400, TYPE_FLOAT, 2.5)})

    values = {}
    for platform in ("PS4", "PS5"):
        response = _post(
            client,
            "ReadStatsByGroups",
            {"ticket": ticket, "Platform": platform, "groupIds": _b64_ids([3])},
        )
        assert response.status_code == 200, response.text
        values[platform] = parse_stats_blob(
            base64.b64decode(_text(_root(response), "Values"), validate=True)
        )
    # PS4 expects INT32 and the FLOAT record is skipped; PS5 uses the Gen9 type.
    assert values["PS4"] == {}
    assert values["PS5"][400].value == 2.5


@pytest.mark.parametrize(
    "group_ids",
    ["not-base64!", base64.b64encode(b"\x00\x01\x00").decode()],
)
def test_read_groups_rejects_malformed_group_ids(client, make_account, group_ids):
    account = make_account(email=f"stats-badgroup-{group_ids[:4]}@example.invalid", nickname=f"badgroup{abs(hash(group_ids))}")
    response = _post(
        client,
        "ReadStatsByGroups",
        {
            "ticket": _ticket(client, account),
            "Platform": "PS4",
            "groupIds": group_ids,
        },
    )
    assert response.status_code == 400


def test_corrupt_stored_blob_is_an_error_not_zero_filled(client, make_account):
    account = make_account(email="stats-corrupt@example.invalid", nickname="statscorrupt")
    ticket = _ticket(client, account)
    store_key = stats_key(_user(account), "PS4")
    from gwsc_local_termux import object_storage

    object_storage.get_object_store().put(store_key, b"not-base64!")
    _seed_lookup(100, group=7)
    response = _post(
        client,
        "ReadStatsByGroups",
        {"ticket": ticket, "Platform": "PS4", "groupIds": _b64_ids([7])},
    )
    assert response.status_code == 500


# ---------------------------------------------------------------------------
# ReadStatsByGamer2
# ---------------------------------------------------------------------------
def test_gamer2_never_serves_another_account(client, make_account):
    owner = make_account(email="stats-gamer2-owner@example.invalid", nickname="gamer2owner")
    other = make_account(email="stats-gamer2-other@example.invalid", nickname="gamer2other")
    ticket = _ticket(client, owner)
    _seed_lookup(100, stat_type=1)
    _seed_blob(owner, "PS4", {100: StatValue(100, TYPE_INT32, 9)})
    _seed_blob(other, "PS4", {100: StatValue(100, TYPE_INT32, 1234)})

    response = _post(
        client,
        "ReadStatsByGamer2",
        {
            "ticket": ticket,
            "Platform": "PS4",
            "gamerHandles": f"NP 2 {owner['profile']['nickname']},NP 2 {other['profile']['nickname']}",
            "statIds": _b64_ids([100]),
        },
    )
    assert response.status_code == 200, response.text
    root = _root(response)
    assert _ns(root) == "ReadStatsResponse"
    results = root.find(".//{*}Results")
    assert results.get("count") == "1"
    rows = results.findall("{*}r")
    assert [row.get("gh") for row in rows] == [f"NP 2 {owner['profile']['nickname']}"]
    decoded = base64.b64decode(rows[0].text or "", validate=True)
    assert decoded == struct.pack(">Bi", TYPE_INT32, 9)


def test_gamer2_requires_stat_ids(client, make_account):
    account = make_account(email="stats-noids@example.invalid", nickname="statsnoids")
    response = _post(
        client,
        "ReadStatsByGamer2",
        {"ticket": _ticket(client, account), "Platform": "PS4", "gamerHandles": "NP 2 player"},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# WriteStats
# ---------------------------------------------------------------------------
def test_write_stats_allowlist_merges_only_accepted_values(client, make_account):
    account = make_account(email="stats-write@example.invalid", nickname="statswrite")
    ticket = _ticket(client, account)
    _seed_lookup(100, stat_type=1)
    _seed_lookup(101, stat_type=1, server_authoritative=True)
    _seed_lookup(102, stat_type=2, raw_type="float")
    _seed_lookup(1314069208, stat_type=8, raw_type="u64")
    _seed_blob(account, "PS4", {777: StatValue(777, TYPE_INT32, 42)})

    incoming = [
        StatValue(100, TYPE_INT32, 5),
        StatValue(101, TYPE_INT32, 6),
        StatValue(102, TYPE_FLOAT, 2.5),
        StatValue(1314069208, TYPE_INT64, 999),
        StatValue(999999, TYPE_INT32, 7),
    ]
    response = _post_write(client, ticket, _packed(incoming))
    assert response.status_code == 200, response.text
    root = _root(response)
    assert _ns(root) == "WriteStatsResponse"
    assert _text(root, "Status") == "1"
    assert _text(root, "NumWritten") == "2"
    assert _text(root, "SecsUntilNextWrite") == "90"
    assert _text(root, "MaxSubmissionBinarySize") == "8192"

    stored = read_stats(_user(account), "PS4")
    assert set(stored) == {100, 102, 777}
    assert stored[100].value == 5
    assert stored[102].value == 2.5
    assert stored[777].value == 42


def test_write_stats_rejects_bad_and_oversized_payloads(client, make_account):
    account = make_account(email="stats-badwrite@example.invalid", nickname="statsbadwrite")
    ticket = _ticket(client, account)

    malformed = client.post(
        f"{LSO}/WriteStats?ticket={ticket}&Platform=PS4",
        content=b"data=\x09not-deflate",
        headers={"Content-Type": "text/plain"},
    )
    assert malformed.status_code == 400

    oversized = client.post(
        f"{LSO}/WriteStats?ticket={ticket}&Platform=PS4",
        content=b"data=" + b"x" * (config.STATS_MAX_SUBMISSION_BYTES + 1),
        headers={"Content-Type": "text/plain"},
    )
    assert oversized.status_code == 413


def test_write_stats_without_seeds_accepts_nothing(client, make_account):
    account = make_account(email="stats-noseeds@example.invalid", nickname="statsnoseeds")
    ticket = _ticket(client, account)
    response = _post_write(
        client, ticket, _packed([StatValue(100, TYPE_INT32, 5)])
    )
    assert response.status_code == 200, response.text
    assert _text(_root(response), "NumWritten") == "0"
    assert read_stats(_user(account), "PS4") == {}
