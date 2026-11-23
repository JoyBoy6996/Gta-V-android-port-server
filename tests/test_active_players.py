"""ActivePlayer writer and v9 schema migration tests."""

import sqlite3
from contextlib import closing

from gwsc_local_termux import config, database
from gwsc_local_termux.time_utils import iso_after, now_iso

LSO = "/gta5/11/gameservices"


def _rows(rockstar_id):
    with closing(database.get_db()) as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT rockstar_id, platform, last_seen FROM active_players "
                "WHERE rockstar_id = ? ORDER BY platform",
                (rockstar_id,),
            ).fetchall()
        ]


def _native_call(client, account, platform=None):
    data = {}
    if platform is not None:
        data["Platform"] = platform
    return client.post(
        f"{LSO}/Presence.asmx/GetAttributes",
        headers=account["headers"],
        data=data,
    )


def test_authenticated_native_request_records_allowlisted_platform(client, make_account):
    account = make_account(email="active@example.invalid", nickname="activeuser")
    rid = account["profile"]["rockstarId"]

    assert _native_call(client, account, "xbox360").status_code in (200, 400)
    rows = _rows(rid)
    assert [row["platform"] for row in rows] == ["XBOX360"]

    # A second observation updates the same row rather than inserting another.
    with closing(database.get_db()) as conn:
        with conn:
            conn.execute(
                "UPDATE active_players SET last_seen = ? WHERE rockstar_id = ?",
                ("2000-01-01T00:00:00+00:00", rid),
            )
    assert _native_call(client, account, "XBOX360").status_code in (200, 400)
    rows = _rows(rid)
    assert len(rows) == 1
    assert rows[0]["last_seen"].startswith("20")


def test_missing_or_unknown_platform_is_not_recorded(client, make_account):
    account = make_account(email="active-missing@example.invalid", nickname="activemiss")
    rid = account["profile"]["rockstarId"]

    _native_call(client, account)
    assert _rows(rid) == []

    _native_call(client, account, "PS2")
    assert _rows(rid) == []


def test_touch_active_player_rejects_invalid_inputs(client):
    with closing(database.get_db()) as conn:
        with conn:
            assert not database.touch_active_player(conn, 42, "PS2")
            assert not database.touch_active_player(conn, 42, "")
            assert not database.touch_active_player(conn, 0, "PS4")
            assert not database.touch_active_player(conn, "not-an-id", "PS4")
            assert database.touch_active_player(conn, 42, " ps5 ")
    assert [row["platform"] for row in _rows(42)] == ["PS5"]


def test_active_player_counts_use_ten_minute_allowlisted_window(client):
    with closing(database.get_db()) as conn:
        with conn:
            database.touch_active_player(conn, 9001, "PS4")
            database.touch_active_player(conn, 9002, "PS4")
            database.touch_active_player(conn, 9003, "PS5")
            database.touch_active_player(conn, 9004, "XBOX360")
            database.touch_active_player(conn, 9005, "XBOXONE")
            database.touch_active_player(conn, 9006, "PCROS")
            database.touch_active_player(conn, 9007, "PS2")
            conn.execute(
                "UPDATE active_players SET last_seen = ? WHERE rockstar_id = ?",
                (iso_after(-601), 9004),
            )
        counts = database.active_player_counts(conn, window_seconds=600)

    assert counts == {
        "PCROS": 1,
        "PS4": 2,
        "PS5": 1,
        "XBOX360": 0,
        "XBOXONE": 1,
        "total": 5,
    }


def test_v8_database_migrates_to_unique_active_players(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(legacy))
    try:
        conn.executescript(
            """
            CREATE TABLE active_players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rockstar_id INTEGER NOT NULL,
                platform TEXT NOT NULL DEFAULT '',
                last_seen TEXT NOT NULL
            );
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            """
        )
        conn.executemany(
            "INSERT INTO active_players (rockstar_id, platform, last_seen) "
            "VALUES (?, ?, ?)",
            [
                (1, "PS4", "2026-01-01T00:00:00+00:00"),
                (1, "PS4", "2026-02-01T00:00:00+00:00"),
                (2, "PS4", "2026-03-01T00:00:00+00:00"),
            ],
        )
        conn.executemany(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            [(v, "2026-01-01T00:00:00+00:00") for v in range(1, 9)],
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(config, "DB_PATH", legacy)
    database.init_db()

    with closing(database.get_db()) as conn:
        rows = conn.execute(
            "SELECT rockstar_id, platform, last_seen FROM active_players "
            "ORDER BY rockstar_id"
        ).fetchall()
        indexes = conn.execute("PRAGMA index_list('active_players')").fetchall()
        migrations = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    assert [(r["rockstar_id"], r["platform"]) for r in rows] == [(1, "PS4"), (2, "PS4")]
    assert rows[0]["last_seen"] == "2026-02-01T00:00:00+00:00"
    assert any(bool(index[2]) and index[3] == "u" for index in indexes)
    assert migrations[-1]["version"] == 9
