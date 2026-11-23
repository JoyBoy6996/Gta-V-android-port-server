"""LSO ProfileStats parity tranche.

``SOURCE_CONFIRMED`` route registrations and XML shapes come from the upstream
LSO source (``src/main.js`` and ``src/endpoints/profilestats/``).  This port is
``ADAPTED`` in four deliberate ways:

1. every endpoint authenticates through the project's account-bound native
   credentials; upstream's ``ReadStatsByGamer2`` trusts client-supplied gamer
   handles to select an arbitrary user, which is not reproduced here;
2. stat ids and wire types come only from imported lookup metadata, and writes
   skip server-authoritative, unsupported and upstream's fabricated-time ids;
3. reads return only values actually stored for the authenticated account --
   no zero-fill, no injected current-time overrides;
4. the RC4 transport, hardcoded platform keys and verbose request logging are
   omitted.

Android acceptance of these envelopes remains ``UNVERIFIED``.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any, Optional

from fastapi import APIRouter, Request, Response

from .. import config
from ..database import get_db
from ..native_requests import (
    field,
    native_path_valid,
    request_values,
    require_user,
    xml_error,
    xml_response,
)
from ..profile_stats_codec import (
    ProfileStatsCodecError,
    StatValue,
    decompress_write_payload,
    expected_wire_type,
    parse_group_ids,
    parse_packed_stats,
    parse_stat_ids,
    write_stats_blob,
    write_stats_only_values,
)
from ..profile_stats_store import (
    ProfileStatsCorruptError,
    ProfileStatsStoreError,
    merge_stats,
    read_stats,
    resolve_platform,
)
from ..xml_protocol import element, lso_response_xml

router = APIRouter(tags=["lso-stats"])

LSO_PREFIX = "/gta5/11/gameservices"
MAX_GAMER_HANDLES = 10
# SOURCE_CONFIRMED: readStatsByGroup.js overwrites these ids with current time.
# They are never fabricated by this port and never accepted from a client.
FABRICATED_TIME_STAT_IDS = frozenset((1314069208, 1553020756, 226991546))
# SOURCE_CONFIRMED: hardcoded values in writeStats.js's response.
SECS_UNTIL_NEXT_WRITE = 90
MAX_SUBMISSION_BINARY_SIZE = 8192

_DATA_FIELD_RE = re.compile(rb"(?:^|&)data=")


async def _stats_request(
    request: Request,
) -> tuple[
    Optional[dict[str, Any]], Optional[dict[str, str]], Optional[str], Optional[Response]
]:
    values = await request_values(request)
    if not native_path_valid(request.path_params):
        return None, None, None, xml_error(400, 400, "Invalid native service path")
    user = require_user(request, values)
    if not user:
        return None, None, None, xml_error(401, 401, "A valid account token is required")
    raw_platform = field(values, "Platform", "platform") or request.headers.get("platform")
    platform = resolve_platform(raw_platform)
    if platform is None:
        return None, None, None, xml_error(400, 400, "Unsupported platform")
    return user, values, platform, None


def _decode_base64_field(raw: Optional[str], name: str) -> bytes:
    if raw is None:
        raise ValueError(f"{name} is required")
    try:
        return base64.b64decode(raw.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{name} is not valid base64") from exc


def _lookup_table(platform: str) -> str:
    # SOURCE_CONFIRMED: only ReadStatsByGroups selects Gen9, for PS5.
    return "gen9_stat_lookups" if platform == "PS5" else "stat_lookups"


def _lookups(
    platform: str, column: str, values: list[object]
) -> dict[int, dict[str, Any]]:
    if not values or column not in ("hash", "group_name"):
        return {}
    table = _lookup_table(platform)
    placeholders = ",".join("?" for _ in values)
    conn = get_db()
    try:
        rows = conn.execute(
            f"SELECT hash, type, flags FROM {table} "
            f"WHERE {column} IN ({placeholders})",
            [str(value) for value in values],
        ).fetchall()
    finally:
        conn.close()
    return {int(row["hash"], 0): row for row in rows}


def _wire_type(row: dict[str, Any]) -> Optional[int]:
    return expected_wire_type(row["type"])


def _server_authoritative(row: dict[str, Any]) -> bool:
    try:
        flags = json.loads(row["flags"] or "{}")
    except (TypeError, ValueError):
        return True  # fail closed if metadata is damaged
    return flags.get("ServerAuthoritative") is True


def _stored_for_lookup(
    stored: dict[int, StatValue], lookups: dict[int, dict[str, Any]]
) -> list[StatValue]:
    """Return only real stored values whose id and type match the lookup."""
    selected: list[StatValue] = []
    for stat_id, row in lookups.items():
        stat = stored.get(stat_id)
        wire = _wire_type(row)
        if stat is None or wire is None or stat.type != wire:
            continue
        selected.append(stat)
    selected.sort(key=lambda item: item.stat_id)
    return selected


def _self_handles(user: dict[str, Any], platform: str) -> set[str]:
    """Derive acceptable handles for this account from real account fields.

    ``ADAPTED``: the NP prefixes and the XBL hex convention are
    source-confirmed handle formats, but the identifiers come from the
    authenticated account's nickname/rockstar id.  Nothing here grants access
    to another account or invents a new player identity.
    """
    nickname = str(user.get("nickname") or "")
    rockstar_id = int(user["rockstar_id"])
    handles = {f"NP 2 {nickname}", f"NP -1 {nickname}", str(rockstar_id)}
    if platform.startswith("XBOX"):
        handles.add(f"XBL {rockstar_id:x}")
        handles.add(f"XBL {rockstar_id:X}")
    return handles


def _response_handle(user: dict[str, Any], platform: str, handle: str) -> str:
    # SOURCE_CONFIRMED: PS4/PS5 normalise every accepted handle to NP 2 <name>.
    if platform in ("PS4", "PS5"):
        return f"NP 2 {user['nickname']}"
    return handle


async def read_stats_by_gamer2(request: Request) -> Response:
    user, values, platform, failure = await _stats_request(request)
    if failure:
        return failure
    assert user is not None and values is not None and platform is not None

    raw_handles = field(values, "gamerHandles", "gamerHandle")
    if raw_handles is None:
        return xml_error(400, 400, "gamerHandles is required")
    try:
        stat_ids = parse_stat_ids(_decode_base64_field(field(values, "statIds"), "statIds"))
    except (ValueError, ProfileStatsCodecError) as exc:
        return xml_error(400, 400, str(exc))

    handles = [part.strip() for part in str(raw_handles).split(",")]
    handles = [part for part in handles if part][:MAX_GAMER_HANDLES]
    lookups = _lookups(platform, "hash", stat_ids)
    try:
        stored = read_stats(user, platform)
    except ProfileStatsCorruptError:
        return xml_error(500, 500, "Stored profile stats are invalid")
    except ProfileStatsStoreError:
        return xml_error(500, 500, "Could not read profile stats")

    selected = _stored_for_lookup(stored, lookups)
    encoded = base64.b64encode(write_stats_only_values(selected)).decode("ascii")
    accepted = _self_handles(user, platform)
    results = [
        element(
            "r",
            encoded,
            attributes={"gh": _response_handle(user, platform, handle)},
        )
        for handle in handles
        if handle in accepted
    ]
    results_element = element(
        "Results", attributes={"count": str(len(results))}, children=results
    )
    return xml_response(
        lso_response_xml([results_element], xmlns="ReadStatsResponse")
    )


async def read_stats_by_groups(request: Request) -> Response:
    user, values, platform, failure = await _stats_request(request)
    if failure:
        return failure
    assert user is not None and values is not None and platform is not None

    try:
        group_ids = parse_group_ids(
            _decode_base64_field(field(values, "groupIds"), "groupIds")
        )
    except (ValueError, ProfileStatsCodecError) as exc:
        return xml_error(400, 400, str(exc))

    lookups = _lookups(platform, "group_name", group_ids)
    try:
        stored = read_stats(user, platform)
    except ProfileStatsCorruptError:
        return xml_error(500, 500, "Stored profile stats are invalid")
    except ProfileStatsStoreError:
        return xml_error(500, 500, "Could not read profile stats")

    selected = _stored_for_lookup(stored, lookups)
    encoded = base64.b64encode(write_stats_blob(selected)).decode("ascii")
    return xml_response(
        lso_response_xml(
            [element("Values", encoded)], xmlns="ReadStatsByGroupsResponse"
        )
    )


def _extract_data_field(raw: bytes) -> bytes:
    """Return the raw tail after ``data=`` (SOURCE_CONFIRMED rawDataExtractor)."""
    match = _DATA_FIELD_RE.search(raw)
    if match is None:
        raise ValueError("data field is required")
    return raw[match.end() :]


async def write_stats(request: Request) -> Response:
    # Read the raw body before the general form parser: urlencoded parsing can
    # consume the stream, and the compressed ``data=`` tail must stay byte-exact.
    raw_body = await request.body()
    user, _values, platform, failure = await _stats_request(request)
    if failure:
        return failure
    assert user is not None and platform is not None

    if len(raw_body) > config.STATS_MAX_SUBMISSION_BYTES:
        return xml_error(413, 413, "Compressed stats are too large")
    try:
        compressed = _extract_data_field(raw_body)
        inflated = decompress_write_payload(
            compressed,
            max_input_bytes=config.STATS_MAX_SUBMISSION_BYTES,
            max_output_bytes=config.STATS_MAX_DECOMPRESSED_BYTES,
        )
        incoming = parse_packed_stats(inflated)
    except (ValueError, ProfileStatsCodecError) as exc:
        return xml_error(400, 400, str(exc))

    lookups = _lookups(platform, "hash", [stat.stat_id for stat in incoming])
    accepted: dict[int, StatValue] = {}
    for stat in incoming:
        row = lookups.get(stat.stat_id)
        wire = _wire_type(row) if row is not None else None
        if (
            row is None
            or wire is None
            or stat.type != wire
            or _server_authoritative(row)
            or stat.stat_id in FABRICATED_TIME_STAT_IDS
        ):
            continue
        accepted[stat.stat_id] = stat

    try:
        merge_stats(user, platform, accepted)
    except ProfileStatsCorruptError:
        return xml_error(500, 500, "Stored profile stats are invalid")
    except ProfileStatsStoreError:
        return xml_error(500, 500, "Could not write profile stats")

    body = lso_response_xml(
        [
            element("NumWritten", str(len(accepted))),
            element("SecsUntilNextWrite", str(SECS_UNTIL_NEXT_WRITE)),
            element("MaxSubmissionBinarySize", str(MAX_SUBMISSION_BINARY_SIZE)),
        ],
        xmlns="WriteStatsResponse",
    )
    return xml_response(body)


async def _authenticate_stats(request: Request):
    values = await request_values(request)
    if not native_path_valid(request.path_params):
        return None, None, xml_error(400, 400, "Invalid native service path")
    user = require_user(request, values)
    if not user:
        return None, None, xml_error(401, 401, "A valid account token is required")
    return user, values, None


async def reset_stats(request: Request) -> Response:
    _user, _values, failure = await _authenticate_stats(request)
    if failure:
        return failure
    # SOURCE_CONFIRMED: ResetStats is a static, status-only response.  The
    # upstream Results attribute is lowercase ``count``.
    return xml_response(
        lso_response_xml(
            [element("Count", "10"), element("Results", attributes={"count": "10"})],
            xmlns="ResetStatsResponse",
        )
    )


_STATS_METHODS = (
    ("ReadStatsByGamer2", read_stats_by_gamer2),
    ("ReadStatsByGroups", read_stats_by_groups),
    ("WriteStats", write_stats),
    ("ResetStats", reset_stats),
)
for _method, _handler in _STATS_METHODS:
    router.add_api_route(
        f"{LSO_PREFIX}/ProfileStats.asmx/{_method}",
        _handler,
        methods=["POST"],
        tags=["lso-stats"],
        name=f"lso_profile_stats_{_method.lower()}",
    )
    router.add_api_route(
        f"/{{title}}/{{version}}/{{service_set}}/ProfileStats.asmx/{_method}",
        _handler,
        methods=["POST"],
        tags=["lso-stats"],
        name=f"lso_profile_stats_{_method.lower()}_native",
    )
    router.add_api_route(
        f"/ProfileStats.asmx/{_method}",
        _handler,
        methods=["POST"],
        tags=["lso-stats"],
        name=f"lso_profile_stats_{_method.lower()}_root",
    )
