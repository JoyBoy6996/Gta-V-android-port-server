"""Opt-in importer for the upstream stat-lookup metadata snapshots.

``SOURCE_CONFIRMED``: upstream LSO seeds ``StatLookup`` and ``Gen9StatLookup``
from two base64-encoded JSONL snapshots in ``prisma/seed-data/``.  The files are
production-exported *metadata only* (stat name, numeric hash, type, group and
flags); they contain no users, sessions, player values or credentials.

This module is the ``ADAPTED`` FastAPI-side loader:

- files are read only from operator-supplied local paths (never fetched);
- hashes are normalised with explicit base handling, so the 65 legacy
  ``0x...`` values are preserved instead of upstream's
  ``parseInt(hash, 10) -> 0`` bug;
- duplicate hashes/names, unexpected fields and partial imports are errors;
- all 24 upstream fields are preserved: six in indexed columns and the other
  18 verbatim in the ``flags`` JSON column;
- both tables are replaced atomically, and the documented row counts (40,849
  legacy / 28,401 Gen9) plus the Gen9-subset-of-legacy relationship are
  verified before any data is written.

Run with ``python -m gwsc_local_termux.stat_seeds --legacy ... --gen9 ...``.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable

from . import config, database

EXPECTED_LEGACY_COUNT = 40_849
EXPECTED_GEN9_COUNT = 28_401

# SOURCE_CONFIRMED: prisma/schema.prisma StatLookup/Gen9StatLookup columns.
UPSTREAM_FIELDS: tuple[str, ...] = (
    "Name",
    "Hash",
    "Type",
    "RawType",
    "Category",
    "Group",
    "Dirty",
    "IsOnlineData",
    "IsProfileStat",
    "IsCommunityStat",
    "ServerAuthoritative",
    "DirtyInThisSession",
    "DownloadPriority",
    "FlushPriority",
    "ReadForRemotePlayers",
    "IsCharacterStat",
    "IsPlayerStat",
    "TriggerEventValueChanged",
    "IsProfileSetting",
    "BlockReset",
    "BlockProfileStatFlush",
    "PlayerInventory",
    "IsNoSaveStat",
    "AlwaysFlush",
)

_COLUMN_FIELDS = frozenset(
    ("Name", "Hash", "Type", "RawType", "Category", "Group")
)
FLAG_FIELDS: tuple[str, ...] = tuple(
    field for field in UPSTREAM_FIELDS if field not in _COLUMN_FIELDS
)

_MAX_NAME_LENGTH = 256
_MAX_RAW_TYPE_LENGTH = 32


class StatSeedError(ValueError):
    """A seed snapshot is malformed, incomplete or inconsistent."""


def normalize_hash(value: object, *, line: int) -> str:
    """Return the canonical decimal stat hash, understanding ``0x`` values."""
    text = str(value).strip() if value is not None else ""
    if not text:
        raise StatSeedError(f"line {line}: empty hash")
    if text.lower().startswith("0x"):
        digits = text[2:]
        if not digits or any(character not in "0123456789abcdefABCDEF" for character in digits):
            raise StatSeedError(f"line {line}: invalid hexadecimal hash")
        parsed = int(digits, 16)
    else:
        if not text.isdigit():
            raise StatSeedError(f"line {line}: invalid decimal hash")
        parsed = int(text, 10)
    if not 0 <= parsed <= 0xFFFFFFFF:
        raise StatSeedError(f"line {line}: hash is outside uint32 range")
    return str(parsed)


def _decode_line(line: str, line_number: int) -> dict[str, Any]:
    encoded = line.strip()
    if not encoded:
        raise StatSeedError(f"line {line_number}: blank seed record")
    try:
        decoded = base64.b64decode(encoded, validate=True)
        record = json.loads(decoded.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StatSeedError(f"line {line_number}: undecodable seed record") from exc
    if not isinstance(record, dict):
        raise StatSeedError(f"line {line_number}: seed record is not an object")
    fields = frozenset(record)
    if fields != frozenset(UPSTREAM_FIELDS):
        missing = sorted(frozenset(UPSTREAM_FIELDS) - fields)
        extra = sorted(fields - frozenset(UPSTREAM_FIELDS))
        raise StatSeedError(
            f"line {line_number}: unexpected seed fields "
            f"(missing={missing[:4]}, extra={extra[:4]})"
        )

    name = record["Name"]
    if not isinstance(name, str) or not name or len(name) > _MAX_NAME_LENGTH:
        raise StatSeedError(f"line {line_number}: invalid stat name")
    raw_type = record["RawType"]
    if not isinstance(raw_type, str) or len(raw_type) > _MAX_RAW_TYPE_LENGTH:
        raise StatSeedError(f"line {line_number}: invalid raw type")

    row: dict[str, Any] = {
        "hash": normalize_hash(record["Hash"], line=line_number),
        "name": name,
        "type": str(int(record["Type"])),
        "raw_type": raw_type,
        "category": str(int(record["Category"])),
        "group_name": str(int(record["Group"])),
        "flags": json.dumps(
            {field: record[field] for field in FLAG_FIELDS},
            sort_keys=True,
            separators=(",", ":"),
        ),
    }
    return row


def _read_snapshot(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        raise StatSeedError(f"seed snapshot does not exist: {path.name}")
    rows: dict[str, dict[str, Any]] = {}
    names: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            row = _decode_line(line, line_number)
            key = row["hash"]
            if key in rows:
                raise StatSeedError(f"line {line_number}: duplicate hash {key}")
            previous_name = names.get(row["name"])
            if previous_name is not None:
                raise StatSeedError(
                    f"line {line_number}: duplicate name {row['name']!r} "
                    f"(hash {previous_name})"
                )
            rows[key] = row
            names[row["name"]] = key
    return rows


_INSERT = (
    "INSERT INTO {table} (hash, name, type, raw_type, category, group_name, flags) "
    "VALUES (:hash, :name, :type, :raw_type, :category, :group_name, :flags)"
)


def import_stat_seeds(
    conn,
    legacy_path: str | Path,
    gen9_path: str | Path,
    *,
    verify_counts: bool = True,
) -> dict[str, int]:
    """Validate both snapshots and replace the local lookup tables atomically."""
    legacy = _read_snapshot(Path(legacy_path))
    gen9 = _read_snapshot(Path(gen9_path))

    if verify_counts:
        if len(legacy) != EXPECTED_LEGACY_COUNT:
            raise StatSeedError(
                f"legacy snapshot has {len(legacy)} rows, expected {EXPECTED_LEGACY_COUNT}"
            )
        if len(gen9) != EXPECTED_GEN9_COUNT:
            raise StatSeedError(
                f"Gen9 snapshot has {len(gen9)} rows, expected {EXPECTED_GEN9_COUNT}"
            )
    missing = set(gen9).difference(legacy)
    if missing:
        sample = sorted(missing)[:4]
        raise StatSeedError(
            f"Gen9 contains {len(missing)} hashes absent from legacy (e.g. {sample})"
        )

    with conn:
        for table in ("stat_lookups", "gen9_stat_lookups"):
            conn.execute(f"DELETE FROM {table}")
            conn.executemany(_INSERT.format(table=table), _ordered_rows(legacy, gen9, table))
    return {"legacy": len(legacy), "gen9": len(gen9)}


def _ordered_rows(
    legacy: dict[str, dict[str, Any]],
    gen9: dict[str, dict[str, Any]],
    table: str,
) -> Iterable[dict[str, Any]]:
    return (legacy if table == "stat_lookups" else gen9).values()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--legacy", default=config.STAT_SEED_LEGACY_PATH)
    parser.add_argument("--gen9", default=config.STAT_SEED_GEN9_PATH)
    parser.add_argument("--db", default="", help="Optional database path override")
    parser.add_argument(
        "--no-verify-counts",
        action="store_true",
        help="Allow smaller snapshots (development/testing only)",
    )
    arguments = parser.parse_args(argv)
    if not arguments.legacy or not arguments.gen9:
        parser.error("--legacy and --gen9 (or their environment variables) are required")

    if arguments.db:
        config.DB_PATH = Path(arguments.db)
    database.init_db()
    with closing(database.get_db()) as conn:
        summary = import_stat_seeds(
            conn,
            arguments.legacy,
            arguments.gen9,
            verify_counts=not arguments.no_verify_counts,
        )
    print(
        f"Imported {summary['legacy']} legacy and {summary['gen9']} Gen9 stat definitions "
        f"into {config.DB_PATH}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI
    raise SystemExit(main())
