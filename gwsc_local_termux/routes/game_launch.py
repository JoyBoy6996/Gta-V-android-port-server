"""Launcher game-launch ticket endpoint.

Contract confirmed from the installed Android client
(``LauncherActivity.requestGameLaunch`` and ``LauncherActivity$98.done``):

    POST /gwsc/api/game-launch
    Authorization: Bearer <launcher token>
    Content-Type: application/json
    {"buildReport": "<opaque client integrity report>"}

    -> {"ticket": "<43 URL-safe characters>"}

The launcher passes the ticket to the game as ``gwscLaunchToken``; the native
layer presents it as ``ScAuthToken``.  Only the ticket hash is stored.  The
ticket is account-bound, expiring and revocable.
"""

import logging
from contextlib import closing
from typing import Any, Dict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from .. import config
from ..auth import require_active_account
from ..database import get_db
from ..errors import api_error
from ..request_utils import rate_key
from ..security import rate_limiter
from ..tickets import (
    GAME_LAUNCH_SERVICE,
    generate_launch_ticket,
    mint_service_ticket,
)

log = logging.getLogger("gwsc.game_launch")
router = APIRouter(tags=["game-launch"])

MAX_BUILD_REPORT_LENGTH = 512


async def _json_object(request: Request) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        raise api_error(400, "invalid_body", "A JSON object body is required")
    if not isinstance(payload, dict):
        raise api_error(400, "invalid_body", "A JSON object body is required")
    return payload


@router.post("/game-launch")
@router.post("/gwsc/api/game-launch")
async def game_launch(
    request: Request,
    current_user: Dict[str, Any] = Depends(require_active_account),
):
    allowed, retry_after = rate_limiter.check(
        rate_key(request, "game-launch", str(current_user["id"])),
        config.GAME_LAUNCH_RATE_LIMIT,
        config.GAME_LAUNCH_RATE_WINDOW,
    )
    if not allowed:
        raise api_error(
            429,
            "rate_limited",
            "Too many launch requests",
            headers={"Retry-After": str(retry_after)},
        )

    payload = await _json_object(request)
    build_report = payload.get("buildReport")
    if not isinstance(build_report, str) or not build_report.strip():
        raise api_error(
            400,
            "invalid_build_report",
            "buildReport must be a non-empty string",
        )
    if len(build_report) > MAX_BUILD_REPORT_LENGTH:
        raise api_error(
            400,
            "invalid_build_report",
            "buildReport is too long",
        )

    raw_ticket = generate_launch_ticket()
    with closing(get_db()) as conn:
        mint_service_ticket(
            conn,
            current_user["id"],
            service=GAME_LAUNCH_SERVICE,
            raw_ticket=raw_ticket,
            platform="android",
            expiry_seconds=config.TICKET_EXPIRY_SECONDS,
            max_per_service=config.MAX_LAUNCH_TICKETS_PER_ACCOUNT,
        )

    log.info(
        "Issued launch ticket for account=%s expires_in=%ss",
        current_user["id"],
        config.TICKET_EXPIRY_SECONDS,
    )
    return JSONResponse(
        {"ticket": raw_ticket},
        headers={"Cache-Control": "no-store"},
    )
