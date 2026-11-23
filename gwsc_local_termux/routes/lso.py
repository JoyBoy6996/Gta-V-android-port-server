"""LSO-compatible native services: stateless, social and clan tranches.

Every endpoint in this module is `SOURCE_CONFIRMED` from the upstream Los
Santos Online source.  We keep the response shapes but replace LSO's
platform-key RC4 transport and permissive credential handling with the
FastAPI project's account-bound native authentication, strict validation and
XML construction.

Android acceptance of these shapes remains `UNVERIFIED`; the route names,
field names and static values are not invented here.  Database-backed
handlers are account-scoped and never trust a client-supplied owner or
recipient identity.
"""

import datetime
import json
import re
import zlib
from contextlib import closing
from typing import Any, Dict, List, Optional, Tuple

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
from ..time_utils import now_iso, parse_iso, utcnow
from ..xml_protocol import element, lso_response_xml, plain_response_xml

router = APIRouter(tags=["lso"])

LSO_PREFIX = "/gta5/11/gameservices"

# SOURCE_CONFIRMED: CashTransactions.asmx/GetPackValueUSDE.
CASH_PACKS: Tuple[Tuple[str, str, str], ...] = (
    ("12EAA58C2A89418F3C29B4E4BD7A47E6", "100000", "3"),
    ("20DE1E16E12DC7C67424E7DA0AE2C0EE", "250000", "5"),
    ("F006EE8B8984283F1AD5BF04E1A0FBF0", "600000", "10"),
    ("A8AA817BAD5FE768166AFF3FCC937F26", "1500000", "20"),
    ("2D2BBF0C70EF90A5136963FABBC329F0", "4250000", "50"),
    ("395975920B28DF2D47C1E35F042699AD", "10000000", "100"),
    ("1EE7703C0DEB4E86ECB4432519A0DDC0", "500000", "0.002"),
    ("5173BC5B225331E7A38FA61A482A0BC0", "300000", "0.0003"),
    ("46188D8AA9B1DB0B1779A72A5FE914CC", "150000", "0.005"),
    ("71ADDA37AB62B96A3AD716A4A3E41E89", "200000", "0.0007"),
    ("ED16274E758A69E44AAA8F3FF19215B6", "100000", "0.0011"),
    ("760A57586B03B0C08B98D2CDEC56AF02", "250000", "0.0012"),
    ("21073CF679BB5B98F54505B27E37552A", "600000", "0.0013"),
    ("60DDE8D58617A798032E8185F5299DF2", "1500000", "0.0014"),
    ("52DBC424960C8A695A859749ACFEAC8C", "4250000", "0.0015"),
    ("68F428DF0203FAD88102D596086AF1FC", "10000000", "0.0016"),
    ("6C1CE4F51330B24DC214B467A085804D", "100000", "2.99"),
    ("E5263B2EF28605AEFB20B7FAEB48FD53", "250000", "4.99"),
    ("91A48A3A7CD02B0675C7F0096B046040", "100000", "3"),
    ("9F98000CCEA2B42961FE59E65C533564", "250000", "5"),
    ("7F0715CCA63304B3467B50FD9C3863DA", "600000", "10"),
    ("BAB1092BAB181C2D72AC8EA1C61447C1", "1500000", "20"),
    ("D1A17327C6DF061366A51A2C356C8B75", "4250000", "50"),
    ("2CE80BF08D93580C06383437155F66CD", "10000000", "100"),
    ("3B3AE867FE5E4670398E88598674581C", "1000000", "0.008"),
    ("2D92DD82C60598470C6170F9A18292DF", "600000", "9.99"),
    ("3E10721F5E3A18B4DEA2AC20083DE1D8", "1500000", "17.995"),
    ("8DD18F886605A15CBD8CCD74A93B252C", "4250000", "44.495"),
    ("DD65DCBD3294E7F31A6390799DDEF6F3", "10000000", "87.995"),
)

CASINO_GAMES: Tuple[str, ...] = (
    "GTAO_CASINO_HOUSE",
    "GTAO_CASINO_3CARDPOKER",
    "GTAO_CASINO_INSIDETRACK",
    "GTAO_CASINO_LUCKYWHEEL",
    "GTAO_CASINO_BLACKJACK",
    "GTAO_CASINO_ROULETTE",
    "GTAO_CASINO_SLOTS",
)

# SOURCE_CONFIRMED: achievements.asmx/GetPlayerAchievements.
ACHIEVEMENTS: Tuple[Tuple[int, str], ...] = (
    (1, "2021-07-23T22:24:38.05"),
    (2, "2023-11-13T01:18:15.37"),
    (6, "2023-11-12T17:14:38.07"),
    (14, "2024-01-16T05:24:05.21"),
    (17, "2024-01-28T01:15:30.53"),
    (22, "2024-01-28T01:15:05.18"),
    (27, "2024-01-28T01:15:39.47"),
    (33, "2021-07-11T04:53:32.69"),
    (34, "2021-07-18T20:03:31.49"),
    (35, "2024-01-28T23:43:40.26"),
    (41, "2021-07-16T03:51:20.86"),
    (46, "2021-07-13T00:36:48.11"),
    (48, "2021-07-14T04:43:53.85"),
    (50, "2024-01-27T21:40:12.46"),
    (51, "2021-07-11T20:05:45.4"),
    (54, "2021-07-14T03:11:32.03"),
)


_PRESENCE_CSV_MAX_LENGTH = 65536
_ATTRIBUTE_NAME_MAX_LENGTH = 128
_ATTRIBUTE_TYPE_MAX_LENGTH = 64
_ATTRIBUTE_VALUE_MAX_LENGTH = 8192
_MESSAGE_MAX_LENGTH = 4096
_MAX_RECIPIENTS = 50
_MAX_NAMES = 256
_QUERY_LIMIT = 10


async def _authenticated_request(
    request: Request,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, str]], Optional[Response]]:
    values = await request_values(request)
    if not native_path_valid(request.path_params):
        return None, None, xml_error(400, 400, "Invalid native service path")
    user = require_user(request, values)
    if not user:
        return None, None, xml_error(401, 401, "A valid account token is required")
    return user, values, None


async def _authenticate(
    request: Request,
) -> Tuple[Optional[Dict[str, Any]], Optional[Response]]:
    user, _values, failure = await _authenticated_request(request)
    return user, failure


def _status_only(*, xmlns: Optional[str] = None, root_attributes=None) -> str:
    return lso_response_xml([], xmlns=xmlns, root_attributes=root_attributes)


def _parse_triples(raw: Optional[str], fields_per_record: int = 3) -> List[Tuple[str, ...]]:
    """Split LSO's literal comma-delimited records.

    ``SOURCE_CONFIRMED``: upstream does a plain ``str.split(',')`` and steps
    three elements at a time.  There is no quoting layer, so a non-multiple of
    three is rejected here instead of silently emitting ``undefined``.
    """
    if not raw:
        return []
    if len(raw) > _PRESENCE_CSV_MAX_LENGTH:
        raise ValueError("CSV is too large")
    parts = raw.split(",")
    if len(parts) % fields_per_record:
        raise ValueError("CSV record length is invalid")
    return [
        tuple(parts[index : index + fields_per_record])
        for index in range(0, len(parts), fields_per_record)
    ]


def _validate_attributes(triples: List[Tuple[str, ...]]) -> None:
    for record in triples:
        value_type, name, value = record
        if not name or len(name) > _ATTRIBUTE_NAME_MAX_LENGTH:
            raise ValueError("Invalid attribute name")
        if not value_type or len(value_type) > _ATTRIBUTE_TYPE_MAX_LENGTH:
            raise ValueError("Invalid attribute type")
        if len(value) > _ATTRIBUTE_VALUE_MAX_LENGTH:
            raise ValueError("Invalid attribute value")


def _write_attributes(
    conn,
    account_id: int,
    triples: List[Tuple[str, ...]],
    *,
    replace: bool,
) -> None:
    now = now_iso()
    if replace:
        conn.execute("DELETE FROM presence_attributes WHERE account_id = ?", (account_id,))
        existing: Dict[str, int] = {}
        next_order = 0
    else:
        rows = conn.execute(
            "SELECT name, sort_order FROM presence_attributes WHERE account_id = ? "
            "ORDER BY sort_order, name",
            (account_id,),
        ).fetchall()
        existing = {row["name"]: row["sort_order"] for row in rows}
        next_order = max(existing.values(), default=-1) + 1

    for value_type, name, value in triples:
        sort_order = existing.get(name)
        if sort_order is None:
            sort_order = next_order
            next_order += 1
        conn.execute(
            """
            INSERT INTO presence_attributes
                (account_id, name, value_type, value_text, value_base64,
                 sort_order, updated_at)
            VALUES (?, ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(account_id, name) DO UPDATE SET
                value_type = excluded.value_type,
                value_text = excluded.value_text,
                value_base64 = NULL,
                sort_order = excluded.sort_order,
                updated_at = excluded.updated_at
            """,
            (account_id, name, value_type, value, sort_order, now),
        )


def _presence_value(conn, account_id: int, name: str) -> str:
    row = conn.execute(
        "SELECT value_text FROM presence_attributes WHERE account_id = ? AND name = ?",
        (account_id, name),
    ).fetchone()
    return row["value_text"] if row and row["value_text"] is not None else ""


def _parse_gamer_handle(handle: str, *, allow_xbl: bool = False) -> int:
    """Resolve an LSO gamer handle to this server's public rockstar id.

    ``ADAPTED``: upstream resolves ``NP``/``SC`` handles through
    ``blueSphereAccountId`` or ``RockstarId``.  This private server has one
    canonical public account number, so both forms resolve to that value.
    """
    cleaned = (handle or "").replace('"', "").strip()
    parts = cleaned.split()
    if len(parts) == 3 and parts[0] == "NP":
        return int(parts[2], 10)
    if len(parts) == 2 and parts[0] == "SC":
        return int(parts[1], 10)
    if allow_xbl and len(parts) >= 2 and parts[0] == "XBL":
        return int(parts[1], 16)
    raise ValueError("Invalid gamer handle")


def _account_by_handle(conn, handle: str, *, allow_xbl: bool = False):
    account_number = _parse_gamer_handle(handle, allow_xbl=allow_xbl)
    return conn.execute(
        "SELECT * FROM users WHERE rockstar_id = ?", (account_number,)
    ).fetchone()


def _platform(request: Request, values: Dict[str, str]) -> str:
    return (field(values, "Platform", "platform") or request.headers.get("platform") or "").upper()


def _epoch_ms(value: Optional[str]) -> str:
    parsed = parse_iso(value)
    return str(int(parsed.timestamp() * 1000)) if parsed else "0"


def _bool_attr(value: Any) -> str:
    return "1" if value else "0"


def _unix_seconds(value: Optional[str]) -> str:
    parsed = parse_iso(value)
    return str(int(parsed.timestamp())) if parsed else "0"


def _player_attributes(row: Any) -> Dict[str, str]:
    return {
        "Id": str(row["rockstar_id"]),
        "RockstarId": str(row["rockstar_id"]),
        "AccountId": str(row["rockstar_id"]),
        "Name": row["nickname"],
        "CrewTag": row["crew_tag"] or "",
        "AvatarUrl": row["avatar"] or "",
    }


def _member_count(conn, clan_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM clan_memberships WHERE clan_id = ?", (clan_id,)
    ).fetchone()[0]


def _clan_attributes(conn, row: Any) -> Any:
    return element(
        "Clan",
        attributes={
            "Id": str(row["id"]),
            "Name": row["name"],
            "Tag": row["tag"],
            "Motto": row["motto"] or "",
            "IsSystemClan": _bool_attr(row["is_system_clan"]),
            "IsOpenClan": _bool_attr(row["is_open_clan"]),
            "CreatedTimePosix": _unix_seconds(row["created_at"]),
            "MemberCount": str(_member_count(conn, row["id"])),
            "Colors": row["colors"] or "Black",
            "IsVerifiedClan": _bool_attr(row["is_verified_clan"]),
        },
    )


def _rank_attributes(row: Any) -> Any:
    if row is None:
        return element("Rank")
    return element(
        "Rank",
        attributes={
            "Id": str(row["id"]),
            "Name": row["name"],
            "RankOrder": str(row["rank_order"]),
            "SystemFlags": str(row["system_flags"]),
        },
    )


def _membership_element(conn, membership: Any, primary_clan_id: Optional[int]) -> Any:
    clan = conn.execute("SELECT * FROM clans WHERE id = ?", (membership["clan_id"],)).fetchone()
    rank = None
    if membership["rank_id"] is not None:
        rank = conn.execute(
            "SELECT * FROM clan_ranks WHERE id = ?", (membership["rank_id"],)
        ).fetchone()
    children = []
    if clan is not None:
        children.append(_clan_attributes(conn, clan))
    children.append(_rank_attributes(rank))
    return element(
        "Membership",
        attributes={
            "Id": str(membership["id"]),
            "IsPrimary": "true" if primary_clan_id == membership["clan_id"] else "false",
            "JoinedTimePosix": _unix_seconds(membership["joined_at"]),
        },
        children=children,
    )


def _parse_clan_id(raw: Optional[str]) -> int:
    try:
        clan_id = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        raise ValueError("Invalid clanId")
    if clan_id <= 0:
        raise ValueError("Invalid clanId")
    return clan_id


# ---------------------------------------------------------------------------
# legalpolicies.asmx
# ---------------------------------------------------------------------------
async def legalpolicies_accepted_version(request: Request) -> Response:
    _user, failure = await _authenticate(request)
    if failure:
        return failure
    return xml_response(
        lso_response_xml(
            [element("Version", "7"), element("PolicyTag", "pp")],
            xmlns="http://services.ros.rockstargames.com/",
        )
    )


# ---------------------------------------------------------------------------
# licenseplates.asmx
# ---------------------------------------------------------------------------
async def licenseplates_get(request: Request) -> Response:
    _user, failure = await _authenticate(request)
    if failure:
        return failure
    return xml_response(
        lso_response_xml(
            [
                element(
                    "Plates",
                    children=[element("Plate", children=[element("PlateText", "JORBY")])],
                )
            ],
            xmlns="Response",
        )
    )


async def licenseplates_is_valid(request: Request) -> Response:
    _user, failure = await _authenticate(request)
    if failure:
        return failure
    return xml_response(
        lso_response_xml(
            [
                element("IsValid", "true"),
                element("IsProfane", "false"),
                element("IsReserved", "false"),
                element("IsMalformed", "false"),
            ],
            xmlns="Response",
        )
    )


# ---------------------------------------------------------------------------
# CashTransactions.asmx
# ---------------------------------------------------------------------------
async def cash_get_pack_value_usde(request: Request) -> Response:
    _user, failure = await _authenticate(request)
    if failure:
        return failure
    packs = [
        element(
            "Pack",
            attributes={
                "id": pack_id,
                "vcValue": vc_value,
                "usde": usde,
                "cashTypeId": "0",
            },
        )
        for pack_id, vc_value, usde in CASH_PACKS
    ]
    return xml_response(
        lso_response_xml(
            [
                element(
                    "ItemList",
                    attributes={"Count": str(len(packs))},
                    children=packs,
                )
            ],
            xmlns="CashValueUSDEResponse",
        )
    )


async def cash_get_legal_territory_restrictions(request: Request) -> Response:
    _user, failure = await _authenticate(request)
    if failure:
        return failure
    restrictions = [
        element(
            "restriction",
            attributes={
                "resId": "GAMBLING_RESTRICTED_GEOGRAPHIC",
                "gameType": game_type,
                "pvcAllowed": "true",
                "evcAllowed": "true",
            },
        )
        for game_type in CASINO_GAMES
    ]
    return xml_response(
        lso_response_xml(
            [
                element(
                    "restrictions",
                    attributes={"territoryCode": "US"},
                    children=restrictions,
                )
            ],
            xmlns="LegalTerritoryRestrictionsResponse",
        )
    )


# ---------------------------------------------------------------------------
# socialclub.asmx / ugc.asmx text and policy checks
# ---------------------------------------------------------------------------
async def socialclub_password_requirements(request: Request) -> Response:
    _user, failure = await _authenticate(request)
    if failure:
        return failure
    return xml_response(
        lso_response_xml(
            [
                element(
                    "Result",
                    children=[
                        element("ValidSymbols", "_*;.-!$^"),
                        element("MinLength", "8"),
                        element("MaxLength", "30"),
                    ],
                )
            ],
            xmlns="GetPasswordRequirements",
        )
    )


async def check_text(request: Request) -> Response:
    _user, failure = await _authenticate(request)
    if failure:
        return failure
    return xml_response(
        lso_response_xml(
            [element("Result", children=[element("Count", "0")])],
            xmlns="CheckText",
        )
    )


# ---------------------------------------------------------------------------
# telemetry.asmx
# ---------------------------------------------------------------------------
async def telemetry_client_config(request: Request) -> Response:
    _user, failure = await _authenticate(request)
    if failure:
        return failure
    return xml_response(
        lso_response_xml(
            [element("RawConfig")],
            xmlns="TelemetryClientConfigResponse",
            root_attributes={
                "IsTelemetryEnabled": "true",
                "Path": "telemetryConfig/gta5_ps4_prod_config.json",
            },
        )
    )


async def telemetry_submit_compressed(request: Request) -> Response:
    _user, failure = await _authenticate(request)
    if failure:
        return failure
    raw = await request.body()
    if not raw or len(raw) > config.MAX_BODY_BYTES:
        return xml_error(400, 400, "Invalid telemetry payload")
    decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
    try:
        # Bound decompression output; telemetry is logged only as a byte count.
        decompressor.decompress(raw, 1024 * 1024)
    except zlib.error:
        return xml_error(400, 400, "Invalid telemetry payload")
    return xml_response(_status_only())


# ---------------------------------------------------------------------------
# achievements.asmx
# ---------------------------------------------------------------------------
async def achievements_synchronize(request: Request) -> Response:
    _user, failure = await _authenticate(request)
    if failure:
        return failure
    return xml_response(_status_only(root_attributes={"ms": "0"}))


async def achievements_get_player(request: Request) -> Response:
    _user, failure = await _authenticate(request)
    if failure:
        return failure
    entries = [
        element(
            "Achievement",
            attributes={
                "Id": str(achievement_id),
                "DateAchieved": date_achieved,
                "AchievedOffline": "false",
            },
        )
        for achievement_id, date_achieved in ACHIEVEMENTS
    ]
    return xml_response(
        lso_response_xml(
            [
                element(
                    "AchievementList",
                    attributes={"Count": str(len(entries))},
                    children=entries,
                )
            ],
            xmlns="PlayerAchievementsResponse",
        )
    )


# ---------------------------------------------------------------------------
# Inbox.asmx / Complaint.asmx / Presence.asmx no-op stubs
# ---------------------------------------------------------------------------
async def inbox_get_unread(request: Request) -> Response:
    _user, failure = await _authenticate(request)
    if failure:
        return failure
    return xml_response(
        lso_response_xml(
            [element("Messages", attributes={"Count": "0", "Total": "0"})],
            xmlns="InboxGetMessagesResponse",
        )
    )


async def complaint_write(request: Request) -> Response:
    _user, failure = await _authenticate(request)
    if failure:
        return failure
    return xml_response(_status_only(root_attributes={"ms": "200"}))


async def presence_noop(request: Request) -> Response:
    _user, failure = await _authenticate(request)
    if failure:
        return failure
    return xml_response(_status_only())


# ---------------------------------------------------------------------------
# entitlements.asmx / AgeAssurance.asmx / ProfileStats.asmx stubs
# ---------------------------------------------------------------------------
async def entitlements_subscription_data_psn(request: Request) -> Response:
    _user, failure = await _authenticate(request)
    if failure:
        return failure
    return xml_response(
        lso_response_xml(
            [
                element(
                    "Subscription",
                    children=[
                        element("Active", "true"),
                        element("SubStartTime", "2024-11-12T11:52:07-06:00"),
                        element("SubEndTime", "2027-11-12T11:52:07-06:00"),
                    ],
                )
            ],
            xmlns="QueryContent",
        )
    )


async def age_assurance_player_status(request: Request) -> Response:
    _user, failure = await _authenticate(request)
    if failure:
        return failure
    return xml_response(
        lso_response_xml(
            [element("AgeAssuranceStatus", "NotRequired")],
            xmlns="PlayerStatus",
        )
    )


# ---------------------------------------------------------------------------
# Presence.asmx: attributes, messages and queries
# ---------------------------------------------------------------------------
async def presence_get_attributes(request: Request) -> Response:
    user, values, failure = await _authenticated_request(request)
    if failure:
        return failure
    handle = field(values, "gamerHandle")
    names_raw = field(values, "namesCsv")
    if handle is None or names_raw is None:
        return xml_error(400, 400, "gamerHandle and namesCsv are required")
    names = names_raw.split(",")
    if not names or len(names) > _MAX_NAMES:
        return xml_error(400, 400, "Invalid namesCsv")
    try:
        with closing(get_db()) as conn:
            target = _account_by_handle(conn, handle)
            if target is None:
                return xml_error(404, 404, "Gamer handle not found")
            pairs: List[str] = []
            for name in names:
                value = _presence_value(conn, target["id"], name) if name else ""
                pairs.extend((name, value))
    except ValueError:
        return xml_error(400, 400, "Invalid gamer handle")
    return xml_response(
        lso_response_xml(
            [
                element("Count", str(len(names))),
                element("NameValueCsv", ",".join(pairs)),
            ],
            xmlns="GetAttributes",
        )
    )


async def presence_set_attributes(request: Request) -> Response:
    user, values, failure = await _authenticated_request(request)
    if failure:
        return failure
    raw = field(values, "typeNameValueCsv")
    try:
        triples = _parse_triples(raw)
        _validate_attributes(triples)
    except ValueError as exc:
        return xml_error(400, 400, str(exc))
    with closing(get_db()) as conn:
        with conn:
            _write_attributes(conn, user["id"], triples, replace=False)
    return xml_response(
        lso_response_xml([], xmlns="http://services.ros.rockstargames.com/")
    )


async def presence_replace_attributes(request: Request) -> Response:
    user, values, failure = await _authenticated_request(request)
    if failure:
        return failure
    raw = field(values, "typeNameValueCsv")
    try:
        triples = _parse_triples(raw)
        _validate_attributes(triples)
    except ValueError as exc:
        return xml_error(400, 400, str(exc))
    with closing(get_db()) as conn:
        with conn:
            _write_attributes(conn, user["id"], triples, replace=True)
    return xml_response(
        lso_response_xml([], xmlns="http://services.ros.rockstargames.com/")
    )


async def presence_get_messages(request: Request) -> Response:
    user, _values, failure = await _authenticated_request(request)
    if failure:
        return failure
    with closing(get_db()) as conn:
        with conn:
            rows = conn.execute(
                "SELECT id, body, created_at FROM social_messages "
                "WHERE recipient_account_id = ? AND delivered_at IS NULL "
                "ORDER BY created_at, id",
                (user["id"],),
            ).fetchall()
            if rows:
                delivered_at = now_iso()
                conn.executemany(
                    "UPDATE social_messages SET delivered_at = ? "
                    "WHERE id = ? AND delivered_at IS NULL",
                    [(delivered_at, row["id"]) for row in rows],
                )
    messages = [
        element(
            "message",
            row["body"],
            attributes={"t": _epoch_ms(row["created_at"]), "svc": "true"},
        )
        for row in rows
    ]
    return xml_response(
        lso_response_xml(
            [
                element(
                    "Messages",
                    attributes={
                        "Count": str(len(messages)),
                        "Total": str(len(messages)),
                        "svc": "true",
                    },
                    children=messages,
                )
            ],
            xmlns="GetPresenceServersResponse",
        )
    )


async def presence_multi_post_message(request: Request) -> Response:
    user, values, failure = await _authenticated_request(request)
    if failure:
        return failure
    message = field(values, "message")
    recipients_raw = field(values, "recipientsCsv") or ""
    if not message or not message.strip() or len(message) > _MESSAGE_MAX_LENGTH:
        return xml_error(400, 400, "Invalid message")
    # SOURCE_CONFIRMED: upstream extracts only double-quoted segments.
    recipients = re.findall(r'"([^"]+)"', recipients_raw)
    if not recipients or len(recipients) > _MAX_RECIPIENTS:
        return xml_error(400, 400, "Invalid recipientsCsv")
    with closing(get_db()) as conn:
        with conn:
            targets: List[int] = []
            for handle in recipients:
                try:
                    target = _account_by_handle(conn, handle, allow_xbl=True)
                except ValueError:
                    return xml_error(400, 400, "Invalid recipient handle")
                if target is None:
                    return xml_error(404, 404, "Recipient not found")
                targets.append(target["id"])
            created_at = now_iso()
            conn.executemany(
                "INSERT INTO social_messages "
                "(sender_account_id, recipient_account_id, body, created_at, "
                "delivered_at, metadata) VALUES (?, ?, ?, ?, NULL, '{}')",
                [(user["id"], target_id, message, created_at) for target_id in targets],
            )
    return xml_response(plain_response_xml([]))


async def presence_query(request: Request) -> Response:
    user, values, failure = await _authenticated_request(request)
    if failure:
        return failure
    query_name = field(values, "queryName")
    if not query_name:
        return xml_error(400, 400, "queryName is required")
    max_records = _QUERY_LIMIT
    raw_limit = field(values, "maxRecordLength")
    if raw_limit is not None:
        try:
            max_records = max(1, min(int(raw_limit), _QUERY_LIMIT))
        except ValueError:
            return xml_error(400, 400, "Invalid maxRecordLength")

    rows: List[Tuple[str, str]] = []
    with closing(get_db()) as conn:
        if query_name == "SessionByGamerHandle":
            try:
                params = _parse_triples(field(values, "paramNameValueCsv"))
            except ValueError as exc:
                return xml_error(400, 400, str(exc))
            gamer_handle = next(
                (value.replace('"', "").strip() for name, value, _ in params if name == "@ghandle"),
                None,
            )
            if not gamer_handle:
                return xml_error(400, 400, "@ghandle is required")
            try:
                target = _account_by_handle(conn, gamer_handle, allow_xbl=True)
            except ValueError:
                return xml_error(400, 400, "Invalid gamer handle")
            if target is not None:
                session = _presence_value(conn, target["id"], "gsinfo")
                if session:
                    rows.append(
                        (
                            gamer_handle,
                            json.dumps({"gsinfo": session}, separators=(",", ":")),
                        )
                    )
        elif query_name == "CrewmateSessions":
            platform = _platform(request, values)
            cutoff = (utcnow() - datetime.timedelta(minutes=5)).isoformat()
            candidates = conn.execute(
                """
                SELECT ap.rockstar_id, ap.platform, u.nickname, pa.value_text AS gsinfo
                FROM active_players AS ap
                JOIN users AS u ON u.rockstar_id = ap.rockstar_id
                JOIN presence_attributes AS pa
                  ON pa.account_id = u.id AND pa.name = 'gsinfo'
                WHERE ap.platform = ? AND ap.last_seen >= ?
                  AND pa.value_text IS NOT NULL AND pa.value_text <> ''
                ORDER BY ap.last_seen DESC
                """,
                (platform, cutoff),
            ).fetchall()
            seen = set()
            for candidate in candidates:
                session = candidate["gsinfo"]
                if session in seen:
                    continue
                seen.add(session)
                if platform in ("PS4", "PS5"):
                    gamer_handle = f'NP 2 "{candidate["nickname"]}"'
                else:
                    gamer_handle = f'XBL {candidate["rockstar_id"]:X}'
                rows.append(
                    (
                        gamer_handle,
                        json.dumps({"gsinfo": session}, separators=(",", ":")),
                    )
                )
                if len(rows) >= max_records:
                    break
        else:
            return xml_error(400, 400, "Unknown presence query")

    return xml_response(
        lso_response_xml(
            [
                element(
                    "Results",
                    attributes={"Count": str(len(rows))},
                    children=[
                        element("r", payload, attributes={"gh": gamer_handle})
                        for gamer_handle, payload in rows
                    ],
                )
            ],
            xmlns="PresenceQueryResponse",
        )
    )


# ---------------------------------------------------------------------------
# Clans.asmx
# ---------------------------------------------------------------------------
async def clans_get_invites(request: Request) -> Response:
    user, _values, failure = await _authenticated_request(request)
    if failure:
        return failure
    invites = []
    with closing(get_db()) as conn:
        rows = conn.execute(
            "SELECT * FROM clan_invites WHERE invitee_id = ? ORDER BY created_at DESC",
            (user["id"],),
        ).fetchall()
        for row in rows:
            inviter = conn.execute(
                "SELECT * FROM users WHERE id = ?", (row["inviter_id"],)
            ).fetchone()
            invitee = conn.execute(
                "SELECT * FROM users WHERE id = ?", (row["invitee_id"],)
            ).fetchone()
            clan = conn.execute(
                "SELECT * FROM clans WHERE id = ?", (row["clan_id"],)
            ).fetchone()
            if inviter is None or invitee is None or clan is None:
                continue
            invites.append(
                element(
                    "Invite",
                    attributes={"Id": str(row["id"]), "Message": row["message"] or ""},
                    children=[
                        element("Inviter", attributes=_player_attributes(inviter)),
                        element("Invitee", attributes=_player_attributes(invitee)),
                        _clan_attributes(conn, clan),
                    ],
                )
            )
    return xml_response(
        lso_response_xml(
            [
                element(
                    "Invites",
                    attributes={
                        "Count": str(len(invites)),
                        "MaxCount": str(len(invites)),
                    },
                    children=invites,
                )
            ],
            xmlns="ClanInviteResponse",
            root_attributes={"ms": "10"},
        )
    )


async def clans_set_primary(request: Request) -> Response:
    user, values, failure = await _authenticated_request(request)
    if failure:
        return failure
    try:
        clan_id = _parse_clan_id(field(values, "clanId"))
    except ValueError as exc:
        return xml_error(400, 400, str(exc))
    with closing(get_db()) as conn:
        with conn:
            membership = conn.execute(
                "SELECT id FROM clan_memberships WHERE user_id = ? AND clan_id = ?",
                (user["id"], clan_id),
            ).fetchone()
            if membership is None:
                return xml_error(403, 403, "Membership not found")
            conn.execute(
                "UPDATE users SET primary_clan_id = ?, updated_at = ? WHERE id = ?",
                (clan_id, now_iso(), user["id"]),
            )
    return xml_response(
        lso_response_xml(
            [], xmlns="ClanMembershipResponse", root_attributes={"ms": "10"}
        )
    )


async def clans_get_primary(request: Request) -> Response:
    user, _values, failure = await _authenticated_request(request)
    if failure:
        return failure
    memberships = []
    with closing(get_db()) as conn:
        primary = conn.execute(
            "SELECT primary_clan_id FROM users WHERE id = ?", (user["id"],)
        ).fetchone()
        primary_clan_id = primary["primary_clan_id"] if primary else None
        if primary_clan_id is not None:
            membership = conn.execute(
                "SELECT * FROM clan_memberships WHERE user_id = ? AND clan_id = ?",
                (user["id"], primary_clan_id),
            ).fetchone()
            if membership is not None:
                memberships.append(_membership_element(conn, membership, primary_clan_id))
    return xml_response(
        lso_response_xml(
            [
                element(
                    "Members",
                    attributes={
                        "Count": str(len(memberships)),
                        "MaxCount": str(len(memberships)),
                    },
                    children=memberships,
                )
            ],
            xmlns="ClanMembershipResponse",
            root_attributes={"ms": "10"},
        )
    )


async def clans_get_metadata(request: Request) -> Response:
    _user, failure = await _authenticate(request)
    if failure:
        return failure
    return xml_response(
        lso_response_xml(
            [element("MetadataEnums", attributes={"Count": "0", "MaxCount": "0"})],
            xmlns="ClanMetadataEnumResponse",
            root_attributes={"ms": "15"},
        )
    )


async def clans_get_mine(request: Request) -> Response:
    user, _values, failure = await _authenticated_request(request)
    if failure:
        return failure
    memberships = []
    with closing(get_db()) as conn:
        primary = conn.execute(
            "SELECT primary_clan_id FROM users WHERE id = ?", (user["id"],)
        ).fetchone()
        primary_clan_id = primary["primary_clan_id"] if primary else None
        rows = conn.execute(
            "SELECT * FROM clan_memberships WHERE user_id = ? "
            "ORDER BY joined_at, id",
            (user["id"],),
        ).fetchall()
        memberships = [
            _membership_element(conn, row, primary_clan_id) for row in rows
        ]
    return xml_response(
        lso_response_xml(
            [
                element(
                    "Memberships",
                    attributes={
                        "Count": str(len(memberships)),
                        "MaxCount": str(len(memberships)),
                    },
                    children=memberships,
                )
            ],
            xmlns="ClanMembershipResponse",
            root_attributes={"ms": "10"},
        )
    )


async def clans_get_desc(request: Request) -> Response:
    _user, values, failure = await _authenticated_request(request)
    if failure:
        return failure
    try:
        clan_id = _parse_clan_id(field(values, "clanId"))
    except ValueError as exc:
        return xml_error(400, 400, str(exc))
    clans = []
    with closing(get_db()) as conn:
        clan = conn.execute("SELECT * FROM clans WHERE id = ?", (clan_id,)).fetchone()
        if clan is not None:
            clans.append(_clan_attributes(conn, clan))
    return xml_response(
        lso_response_xml(
            [
                element(
                    "Clans",
                    attributes={"Count": str(len(clans)), "MaxCount": str(len(clans))},
                    children=clans,
                )
            ],
            xmlns="ClanMembershipResponse",
            root_attributes={"ms": "10"},
        )
    )


async def leaderboards_read_by_crews(request: Request) -> Response:
    _user, failure = await _authenticate(request)
    if failure:
        return failure
    return xml_response(
        plain_response_xml(
            [element("Results", attributes={"count": "0", "total": "0"})]
        )
    )


# ---------------------------------------------------------------------------
# Route registration: exact LSO, generic native and root aliases
# ---------------------------------------------------------------------------
_ROUTES: Tuple[Tuple[str, str, Any], ...] = (
    ("legalpolicies.asmx", "GetAcceptedVersion", legalpolicies_accepted_version),
    ("licenseplates.asmx", "Get", licenseplates_get),
    ("licenseplates.asmx", "IsValid", licenseplates_is_valid),
    ("CashTransactions.asmx", "GetPackValueUSDE", cash_get_pack_value_usde),
    (
        "CashTransactions.asmx",
        "GetLegalTerritoryRestrictions",
        cash_get_legal_territory_restrictions,
    ),
    ("socialclub.asmx", "GetPasswordRequirements", socialclub_password_requirements),
    ("socialclub.asmx", "CheckText", check_text),
    ("ugc.asmx", "CheckText", check_text),
    ("telemetry.asmx", "GetTelemetryClientConfig", telemetry_client_config),
    ("telemetry.asmx", "SubmitCompressed", telemetry_submit_compressed),
    ("achievements.asmx", "Synchronize", achievements_synchronize),
    ("achievements.asmx", "GetPlayerAchievements", achievements_get_player),
    ("Inbox.asmx", "GetUnreadMessages", inbox_get_unread),
    ("Inbox.asmx", "PostMessageToRecipients", presence_noop),
    ("Complaint.asmx", "WriteComplaint", complaint_write),
    ("entitlements.asmx", "GetSubscriptionDataPSN", entitlements_subscription_data_psn),
    ("AgeAssurance.asmx", "PlayerStatus", age_assurance_player_status),
    ("Presence.asmx", "Publish", presence_noop),
    ("Presence.asmx", "PostMessageToRecipients", presence_noop),
    ("Presence.asmx", "GetAttributesForGamers", presence_noop),
    ("Presence.asmx", "Subscribe", presence_noop),
    ("Presence.asmx", "Unsubscribe", presence_noop),
    ("Presence.asmx", "SignOut", presence_noop),
    ("Presence.asmx", "GetAttributes", presence_get_attributes),
    ("Presence.asmx", "SetAttributes", presence_set_attributes),
    ("Presence.asmx", "ReplaceAttributes", presence_replace_attributes),
    ("Presence.asmx", "GetMessages", presence_get_messages),
    ("Presence.asmx", "MultiPostMessage", presence_multi_post_message),
    ("Presence.asmx", "Query", presence_query),
    ("Presence.asmx", "QueryWithMaxRecordLength", presence_query),
    ("Clans.asmx", "GetInvites", clans_get_invites),
    ("Clans.asmx", "SetPrimaryClan", clans_set_primary),
    ("Clans.asmx", "GetPrimaryClans", clans_get_primary),
    ("Clans.asmx", "GetMetadataForClan", clans_get_metadata),
    ("Clans.asmx", "GetMine", clans_get_mine),
    ("Clans.asmx", "GetDesc", clans_get_desc),
    ("Leaderboards.asmx", "ReadByCrews", leaderboards_read_by_crews),
)

for _service, _method, _handler in _ROUTES:
    _route_name = f"lso_{_service.replace('.', '_')}_{_method}"
    router.add_api_route(
        f"{LSO_PREFIX}/{_service}/{_method}",
        _handler,
        methods=["POST"],
        tags=["lso"],
        name=_route_name,
    )
    router.add_api_route(
        f"/{{title}}/{{version}}/{{service_set}}/{_service}/{_method}",
        _handler,
        methods=["POST"],
        tags=["lso"],
        name=f"{_route_name}_native",
    )
    router.add_api_route(
        f"/{_service}/{_method}",
        _handler,
        methods=["POST"],
        tags=["lso"],
        name=f"{_route_name}_root",
    )
