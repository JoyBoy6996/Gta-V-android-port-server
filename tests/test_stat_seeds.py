"""Tests for the opt-in stat-definition seed importer.

Synthetic snapshots are generated locally with all 24 source-confirmed fields;
the real 40k/28k upstream metadata files are not vendored or needed here.
"""

import base64
import json
from contextlib import closing

import pytest

from gwsc_local_termux import config, database
from gwsc_local_termux.stat_seeds import (
    EXPECTED_GEN9_COUNT,
    EXPECTED_LEGACY_COUNT,
    FLAG_FIELDS,
    UPSTREAM_FIELDS,
    StatSeedError,
    import_stat_seeds,
    main,
    normalize_hash,
)


def _record(name, hash_value, *, group=1, stat_type=1, **overrides):
    record = {field: False for field in UPSTREAM_FIELDS}
    record.update(
        {
            "Name": name,
            "Hash": hash_value,
            "Type": stat_type,
            "RawType": "int",
            "Category": 0,
            "Group": group,
            "ServerAuthoritative": False,
        }
    )
    record.update(overrides)
    return record


def _snapshot(path, records):
    lines = [
        base64.b64encode(json.dumps(record, separators=(",", ":")).encode()).decode()
        for record in records
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _rows(table):
    with closing(database.get_db()) as conn:
        return conn.execute(f"SELECT * FROM {table} ORDER BY hash").fetchall()


@pytest.mark.parametrize(
    "raw,expected",
    [("123", "123"), ("0x7b", "123"), ("0XFB", "251"), (4294967295, "4294967295")],
)
def test_normalize_hash_understands_decimal_and_hex(raw, expected):
    assert normalize_hash(raw, line=1) == expected


@pytest.mark.parametrize("raw", ["", "0x", "0xZZ", "12.5", "-1", 2**32])
def test_normalize_hash_rejects_invalid_values(raw):
    with pytest.raises(StatSeedError):
        normalize_hash(raw, line=7)


def test_import_maps_columns_and_preserves_all_flags(client, tmp_path):
    legacy = _snapshot(
        tmp_path / "legacy.b64",
        [
            _record("BANK_BALANCE", "2417560051", group=7, stat_type=26, RawType="long"),
            _record("HEX_HASH", "0xE29C994A", group=0, stat_type=4, BlockReset=True),
            _record("THIRD", "30313", group=1, stat_type=2, RawType="float"),
        ],
    )
    gen9 = _snapshot(
        tmp_path / "gen9.b64",
        [
            _record("BANK_BALANCE", "2417560051", group=0, stat_type=1, RawType="long"),
            _record("THIRD", "30313", group=1, stat_type=2, RawType="float"),
        ],
    )
    with closing(database.get_db()) as conn:
        summary = import_stat_seeds(conn, legacy, gen9, verify_counts=False)
    assert summary == {"legacy": 3, "gen9": 2}

    legacy_rows = _rows("stat_lookups")
    assert [row["hash"] for row in legacy_rows] == ["2417560051", "30313", "3801913674"]
    hex_row = legacy_rows[2]
    assert hex_row["name"] == "HEX_HASH"
    assert hex_row["type"] == "4"
    assert hex_row["group_name"] == "0"
    assert set(json.loads(hex_row["flags"])) == set(FLAG_FIELDS)
    assert json.loads(hex_row["flags"])["BlockReset"] is True

    gen9_rows = _rows("gen9_stat_lookups")
    assert [row["hash"] for row in gen9_rows] == ["2417560051", "30313"]
    # SOURCE_CONFIRMED: the same hash can have a different type on Gen9.
    assert gen9_rows[0]["type"] == "1"
    assert legacy_rows[0]["type"] == "26"


def test_import_is_atomic_when_snapshots_are_inconsistent(client, tmp_path):
    legacy = _snapshot(
        tmp_path / "legacy.b64",
        [_record("ONE", "1"), _record("TWO", "2")],
    )
    gen9 = _snapshot(tmp_path / "gen9.b64", [_record("ONE", "1"), _record("GHOST", "999")])
    with closing(database.get_db()) as conn:
        with pytest.raises(StatSeedError, match="absent from legacy"):
            import_stat_seeds(conn, legacy, gen9, verify_counts=False)
    assert _rows("stat_lookups") == []
    assert _rows("gen9_stat_lookups") == []


@pytest.mark.parametrize(
    "records,message",
    [
        ([_record("ONE", "1"), _record("ONE", "2")], "duplicate name"),
        ([_record("ONE", "1"), _record("TWO", "1")], "duplicate hash"),
        ([_record("ONE", "0xGG")], "invalid hexadecimal hash"),
    ],
)
def test_import_rejects_bad_snapshots(client, tmp_path, records, message):
    legacy = _snapshot(tmp_path / "legacy.b64", records)
    gen9 = _snapshot(tmp_path / "gen9.b64", [])
    with closing(database.get_db()) as conn:
        with pytest.raises(StatSeedError, match=message):
            import_stat_seeds(conn, legacy, gen9, verify_counts=False)


def test_import_rejects_wrong_field_set_and_bad_base64(client, tmp_path):
    bad_fields = _record("ONE", "1")
    del bad_fields["ServerAuthoritative"]
    legacy = _snapshot(tmp_path / "legacy.b64", [bad_fields])
    gen9 = _snapshot(tmp_path / "gen9.b64", [])
    with closing(database.get_db()) as conn:
        with pytest.raises(StatSeedError, match="unexpected seed fields"):
            import_stat_seeds(conn, legacy, gen9, verify_counts=False)

    legacy.write_text("not-base64!\n", encoding="utf-8")
    with closing(database.get_db()) as conn:
        with pytest.raises(StatSeedError, match="undecodable"):
            import_stat_seeds(conn, legacy, gen9, verify_counts=False)


def test_documented_counts_are_enforced(client, tmp_path):
    legacy = _snapshot(tmp_path / "legacy.b64", [_record("ONE", "1")])
    gen9 = _snapshot(tmp_path / "gen9.b64", [_record("ONE", "1")])
    with closing(database.get_db()) as conn:
        with pytest.raises(StatSeedError, match=str(EXPECTED_LEGACY_COUNT)):
            import_stat_seeds(conn, legacy, gen9)
    assert EXPECTED_GEN9_COUNT < EXPECTED_LEGACY_COUNT


def test_cli_imports_with_explicit_counts_disabled(client, tmp_path, monkeypatch, capsys):
    legacy = _snapshot(tmp_path / "legacy.b64", [_record("ONE", "1", group=5)])
    gen9 = _snapshot(tmp_path / "gen9.b64", [_record("ONE", "1", group=5)])
    db_path = tmp_path / "cli.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    exit_code = main(
        [
            "--legacy",
            str(legacy),
            "--gen9",
            str(gen9),
            "--db",
            str(db_path),
            "--no-verify-counts",
        ]
    )
    assert exit_code == 0
    assert "Imported 1 legacy and 1 Gen9" in capsys.readouterr().out
    assert [row["group_name"] for row in _rows("stat_lookups")] == ["5"]
