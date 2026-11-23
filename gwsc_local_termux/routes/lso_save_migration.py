"""LSO SaveMigration stubs, adapted for a private account-bound server.

``SOURCE_CONFIRMED`` from upstream LSO:

- ``SaveMigration.asmx/GetSourceAccountsMP`` returns a constant
  ``<Result><Accounts></Accounts></Result>`` with ``Status=1``;
- ``SaveMigrationSingleplayer.asmx/GetUploadedSingleplayerSaveMetadata`` returns
  a single ``SaveMigrationRecordMetadata`` block, with ``Status`` after it.

Neither upstream handler reads a body, an S3 object or any save state.  The
singleplayer handler leaks ``User.SessionKey`` in ``SaveMigrationRecordToken``
and fills both timestamps with the current request time.

``ADAPTED`` here: both endpoints authenticate the caller, no session key or
other secret is emitted, and no upload/save time is invented.  Constants that
are source-confirmed response literals are copied; fields with no real stored
value are explicit neutral placeholders.  Android acceptance is ``UNVERIFIED``.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request, Response

from ..native_requests import (
    native_path_valid,
    request_values,
    require_user,
    xml_error,
    xml_response,
)
from ..xml_protocol import bare_method_response_xml, element

router = APIRouter(tags=["lso-save-migration"])

LSO_PREFIX = "/gta5/11/gameservices"

# SOURCE_CONFIRMED: hardcoded literals in getSinglePlayerSaveState.js:14-23.
SOURCE_PLAYER_ACCOUNT_ID = 42069
SOURCE_PLATFORM_ID = 3
COMPLETION_PERCENTAGE = 0
LAST_COMPLETED_MISSION_ID = 0
# ADAPTED: upstream reports request time as an upload/save time although no
# save is read.  Zero is an explicit "no stored migration record" sentinel.
NO_RECORDED_TIME = 0


async def _authenticate(request: Request):
    values = await request_values(request)
    if not native_path_valid(request.path_params):
        return None, xml_error(400, 400, "Invalid native service path")
    user = require_user(request, values)
    if not user:
        return None, xml_error(401, 401, "A valid account token is required")
    return user, None


async def get_source_accounts_mp(request: Request) -> Response:
    _user, failure = await _authenticate(request)
    if failure:
        return failure
    result = element("Result", children=[element("Accounts")])
    return xml_response(
        bare_method_response_xml("GetSourceAccountsMP", [result])
    )


async def get_uploaded_singleplayer_save_metadata(request: Request) -> Response:
    user, failure = await _authenticate(request)
    if failure:
        return failure
    # SOURCE_CONFIRMED element order; the token is deliberately empty because
    # upstream's value is the raw RC4 SessionKey credential.
    metadata = element(
        "SaveMigrationRecordMetadata",
        children=[
            element("SaveMigrationRecordToken", ""),
            element("SourceRockstarId", str(user["rockstar_id"])),
            element("SourcePlayerAccountId", str(SOURCE_PLAYER_ACCOUNT_ID)),
            element("SourcePlatformId", str(SOURCE_PLATFORM_ID)),
            element("UploadPosixTime", str(NO_RECORDED_TIME)),
            element("CompletionPercentage", str(COMPLETION_PERCENTAGE)),
            element("LastCompletedMissionId", str(LAST_COMPLETED_MISSION_ID)),
            element("SavePosixTime", str(NO_RECORDED_TIME)),
        ],
    )
    return xml_response(
        bare_method_response_xml("GetUploadedSingleplayerSaveMetadata", [metadata])
    )


_METHODS = (
    ("SaveMigration.asmx", "GetSourceAccountsMP", get_source_accounts_mp),
    (
        "SaveMigrationSingleplayer.asmx",
        "GetUploadedSingleplayerSaveMetadata",
        get_uploaded_singleplayer_save_metadata,
    ),
)
for _service, _method, _handler in _METHODS:
    _name = f"lso_{_service.replace('.', '_')}_{_method.lower()}"
    router.add_api_route(
        f"{LSO_PREFIX}/{_service}/{_method}",
        _handler,
        methods=["POST"],
        tags=["lso-save-migration"],
        name=_name,
    )
    router.add_api_route(
        f"/{{title}}/{{version}}/{{service_set}}/{_service}/{_method}",
        _handler,
        methods=["POST"],
        tags=["lso-save-migration"],
        name=f"{_name}_native",
    )
    router.add_api_route(
        f"/{_service}/{_method}",
        _handler,
        methods=["POST"],
        tags=["lso-save-migration"],
        name=f"{_name}_root",
    )
