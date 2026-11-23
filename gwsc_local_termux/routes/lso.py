"""Stateless LSO-compatible native services.

Every endpoint in this module is `SOURCE_CONFIRMED` from the upstream Los
Santos Online source.  We keep the response shapes but replace LSO's
platform-key RC4 transport and permissive credential handling with the
FastAPI project's account-bound native authentication and XML validation.

Android acceptance of these shapes remains `UNVERIFIED`; the route names,
field names and static values are not invented here.
"""

import zlib
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Request, Response

from .. import config
from ..native_requests import (
    native_path_valid,
    request_values,
    require_user,
    xml_error,
    xml_response,
)
from ..xml_protocol import element, lso_response_xml

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


async def _authenticate(request: Request) -> Tuple[Optional[Dict[str, Any]], Optional[Response]]:
    values = await request_values(request)
    if not native_path_valid(request.path_params):
        return None, xml_error(400, 400, "Invalid native service path")
    user = require_user(request, values)
    if not user:
        return None, xml_error(401, 401, "A valid account token is required")
    return user, None


def _status_only(*, xmlns: Optional[str] = None, root_attributes=None) -> str:
    return lso_response_xml([], xmlns=xmlns, root_attributes=root_attributes)


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


async def profile_stats_reset(request: Request) -> Response:
    _user, failure = await _authenticate(request)
    if failure:
        return failure
    return xml_response(
        lso_response_xml(
            [
                element("Count", "10"),
                element("Results", attributes={"Count": "10"}),
            ],
            xmlns="ResetStatsResponse",
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
    ("ProfileStats.asmx", "ResetStats", profile_stats_reset),
    ("Presence.asmx", "Publish", presence_noop),
    ("Presence.asmx", "PostMessageToRecipients", presence_noop),
    ("Presence.asmx", "GetAttributesForGamers", presence_noop),
    ("Presence.asmx", "Subscribe", presence_noop),
    ("Presence.asmx", "Unsubscribe", presence_noop),
    ("Presence.asmx", "SignOut", presence_noop),
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
