"""LSO cloud persistence parity: saves, news, UGC binaries and photo upload.

``SOURCE_CONFIRMED``: upstream Los Santos Online uses S3-backed routes for
save files, profile shares, news assets and UGC binaries, with an HMAC token
for photo uploads.  This port is ``ADAPTED``: it keeps source-confirmed route
names, response shapes and key derivations where safe, but replaces upstream's
RC4 transport and unverified path-based identity with this project's
account-bound native authentication.  Android acceptance remains
``UNVERIFIED``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import urllib.parse
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from .. import config
from ..auth import authenticate_native_request
from ..database import get_db
from ..native_requests import (
    field,
    request_values,
    require_user,
    session_ticket_header,
)
from ..object_storage import (
    ObjectNotFoundError,
    ObjectStorageConfigurationError,
    ObjectStorageError,
    account_key,
    get_object_store,
    validate_key,
)
from ..time_utils import now_iso, parse_iso
from ..xml_protocol import cloud_error_xml, cloud_status_xml

router = APIRouter(tags=["lso-cloud"])

XML_CONTENT_TYPE = "application/xml"
TEXT_CONTENT_TYPE = "text/plain; charset=utf-8"
GLOBAL_CACHE = "public, max-age=0, immutable"

# SOURCE_CONFIRMED: upstream's platform-to-S3-prefix map.
PLATFORM_PREFIXES = {
    "PCROS": "PCROS",
    "XBOXONE": "XBOXONE",
    "XBOX360": "XBOX360",
    "PS4": "PS4",
    "PS5": "PS5",
    "PS3": "PS3",
}
PATH_PLATFORM_MAP = {"xbl": "XBOXONE", "sc": "PCROS", "np": "PS4"}

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
CONTENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_MIME_RE = re.compile(r"^[A-Za-z0-9.+/-]{1,64}$")
STRICT_INT_RE = re.compile(r"^[0-9]+$")


def _xml(content: str, status_code: int = 200, headers=None) -> Response:
    return Response(
        content=content,
        status_code=status_code,
        media_type=XML_CONTENT_TYPE,
        headers=headers,
    )


def _text(message: str, status_code: int = 200, headers=None) -> Response:
    return Response(
        content=message,
        status_code=status_code,
        media_type=TEXT_CONTENT_TYPE,
        headers=headers,
    )


async def _native_user(request: Request) -> Optional[Dict[str, Any]]:
    values = await request_values(request)
    user = require_user(request, values)
    return user if user and user.get("status") == "active" else None


def _photo_user(request: Request) -> Optional[Dict[str, Any]]:
    """Authenticate photo uploads without treating the HMAC token as a ticket."""
    user = authenticate_native_request(
        query_token=request.query_params.get("scAuthToken"),
        authorization=request.headers.get("authorization"),
        header_token=request.headers.get("x-gwsc-token"),
        session_ticket=session_ticket_header(request),
    )
    return user if user and user.get("status") == "active" else None


def _safe_component(raw: Optional[str], label: str) -> str:
    if not raw or not SAFE_NAME_RE.fullmatch(raw) or raw in (".", ".."):
        raise ValueError(f"Invalid {label}")
    validate_key((raw,))
    return raw


def _safe_path_id(raw: Optional[str], label: str, pattern=SAFE_ID_RE) -> str:
    if not raw or not pattern.fullmatch(raw):
        raise ValueError(f"Invalid {label}")
    return raw


def _platform(path_platform: str, values: Dict[str, str], request: Request) -> str:
    override = field(values, "Platform", "platform") or request.headers.get("platform")
    if override:
        platform = override.strip().upper()
        if platform not in PLATFORM_PREFIXES:
            raise ValueError("Invalid platform")
        return platform
    try:
        return PATH_PLATFORM_MAP[path_platform.lower()]
    except KeyError:
        raise ValueError("Invalid platform")


async def _first_upload_part(request: Request) -> Tuple[str, bytes, str]:
    """Return (filename, bytes, content_type) from the first multipart file."""
    form = await request.form()
    for _name, value in form.multi_items():
        filename = getattr(value, "filename", None)
        if not filename:
            continue
        data = await value.read() if hasattr(value, "read") else str(value).encode()
        content_type = getattr(value, "content_type", None) or "application/octet-stream"
        return _safe_component(filename, "filename"), bytes(data), _safe_content_type(content_type)
    raise ValueError("A multipart file part is required")


def _safe_content_type(raw: Optional[str]) -> str:
    if not raw:
        return "application/octet-stream"
    value = raw.split(";", 1)[0].strip()
    if not SAFE_MIME_RE.fullmatch(value):
        raise ValueError("Invalid content type")
    return value


def _store_error_response(exc: ObjectStorageError) -> Response:
    # ObjectStorageError messages are internal; return a stable generic body.
    return _xml(
        cloud_error_xml("InternalError", "Server error occurred"),
        status_code=500,
    )


# ---------------------------------------------------------------------------
# Save files and profile image shares
# ---------------------------------------------------------------------------
async def save_file_upload(request: Request) -> Response:
    user = await _native_user(request)
    if not user:
        return _xml(cloud_error_xml("Unauthorized", ""), status_code=401)

    path_id = request.path_params.get("account_id", "")
    if not SAFE_ID_RE.fullmatch(path_id):
        return _xml(cloud_error_xml("InvalidPath", "Invalid account id"), status_code=400)

    values = await request_values(request)
    try:
        platform = _platform(request.path_params["platform"], values, request)
        filename, payload, _content_type = await _first_upload_part(request)
        key = account_key(user["id"], "saves", platform, filename)
    except ValueError as exc:
        return _xml(cloud_error_xml("InvalidRequest", str(exc)), status_code=400)

    try:
        get_object_store().put(key, payload, content_type="application/octet-stream")
    except ObjectStorageError as exc:
        return _store_error_response(exc)
    return _xml(cloud_status_xml(status=1), headers={"Cache-Control": GLOBAL_CACHE})


async def save_file_download(request: Request) -> Response:
    user = await _native_user(request)
    if not user:
        return _xml(
            cloud_error_xml("Unauthorized", ""),
            status_code=401,
            headers={"Content-Type": "application/octet-stream"},
        )

    values = await request_values(request)
    try:
        platform = _platform(request.path_params["platform"], values, request)
        filename = _safe_component(request.path_params.get("saveFileName"), "saveFileName")
        key = account_key(user["id"], "saves", platform, filename)
    except ValueError as exc:
        return _xml(
            cloud_error_xml("InvalidRequest", str(exc)),
            status_code=400,
            headers={"Content-Type": "application/octet-stream"},
        )

    try:
        raw = get_object_store().get(key)
    except ObjectNotFoundError:
        return _xml(
            cloud_error_xml("DoesNotExist", ""),
            status_code=404,
            headers={"Content-Type": "application/octet-stream"},
        )
    except ObjectStorageError as exc:
        return _store_error_response(exc)
    return Response(
        content=raw,
        media_type="application/octet-stream",
        headers={"Cache-Control": "private, max-age=0"},
    )


async def profile_share_upload(request: Request) -> Response:
    user = await _native_user(request)
    if not user:
        return _xml(cloud_error_xml("Unauthorized", ""), status_code=401)

    # SOURCE_CONFIRMED: upstream ignores :scId for identity but writes under
    # its ps4/ prefix.  We validate the path value yet still scope storage to
    # the authenticated account so a mismatched id cannot overwrite another
    # player's share image.
    if not SAFE_ID_RE.fullmatch(request.path_params.get("scId", "")):
        return _xml(cloud_error_xml("InvalidPath", "Invalid scId"), status_code=400)
    try:
        filename, payload, content_type = await _first_upload_part(request)
        key = account_key(user["id"], "profile-share", "PS4", filename)
    except ValueError as exc:
        return _xml(cloud_error_xml("InvalidRequest", str(exc)), status_code=400)

    try:
        get_object_store().put(key, payload, content_type=content_type)
    except ObjectStorageError as exc:
        return _store_error_response(exc)
    return _xml(cloud_status_xml(status=1), headers={"Cache-Control": GLOBAL_CACHE})


# ---------------------------------------------------------------------------
# News JSON, story and image
# ---------------------------------------------------------------------------
def _epoch(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        parsed = parse_iso(str(value))
        return int(parsed.timestamp()) if parsed else 0


def _json_types(raw: Any) -> list:
    if not raw:
        return []
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _json_extra(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def news_items(request: Request) -> Response:
    if not await _native_user(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    now = int(time.time())
    items = []
    with closing(get_db()) as conn:
        rows = conn.execute(
            "SELECT * FROM news WHERE active = 1 "
            "ORDER BY priority DESC, secondary_priority DESC, date DESC"
        ).fetchall()
    for row in rows:
        date = _epoch(row["date"])
        expire = _epoch(row["expire"])
        if date > now or (0 < expire <= now):
            continue
        items.append(
            {
                "gm.evt": {
                    "e": "news",
                    "d": {
                        "ts": date,
                        "exp": expire,
                        "t": _json_types(row["types"]),
                        "k": row["story_key"] or "",
                        "p": int(row["priority"] or 0),
                        "sp": int(row["secondary_priority"] or 0),
                        "sk": row["tracking_id"] or "",
                    },
                }
            }
        )
    return JSONResponse(items, headers={"Cache-Control": GLOBAL_CACHE})


async def news_story(request: Request) -> Response:
    if not await _native_user(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        story_key = _safe_path_id(
            request.path_params.get("storyKey"), "storyKey", SAFE_NAME_RE
        )
        # SOURCE_CONFIRMED: langFile is accepted but ignored upstream.
        _safe_path_id(request.path_params.get("langFile"), "langFile", SAFE_NAME_RE)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    now = int(time.time())
    with closing(get_db()) as conn:
        row = conn.execute(
            "SELECT * FROM news WHERE story_key = ? AND active = 1 LIMIT 1",
            (story_key,),
        ).fetchone()
    if row is None:
        return JSONResponse({"error": "News story not found"}, status_code=404)
    date = _epoch(row["date"])
    expire = _epoch(row["expire"])
    if date > now or (0 < expire <= now):
        return JSONResponse({"error": "News story not available"}, status_code=404)

    payload: Dict[str, Any] = {
        "types": _json_types(row["types"]),
        "date": date,
        "expire": expire,
        "title": row["title"] or "",
        "content": row["content"] or "",
        "style": row["style"] or "",
        "priority": int(row["priority"] or 0),
        "secondaryPriority": int(row["secondary_priority"] or 0),
        "trackingId": row["tracking_id"] or "",
    }
    for column, key in (("headline", "headline"), ("subtitle", "subtitle"), ("url", "url")):
        if row[column]:
            payload[key] = row[column]
    if row["image_path"]:
        payload["image"] = {
            "path": row["image_path"],
            "filesize": int(row["image_filesize"] or 0),
            "type": row["image_type"] or "portrait",
        }
    extra = _json_extra(row["extra_data"])
    if extra:
        payload["extraData"] = extra
    return JSONResponse(payload, headers={"Cache-Control": GLOBAL_CACHE})


async def news_image(request: Request) -> Response:
    if not await _native_user(request):
        return _text("Unauthorized", status_code=401)
    try:
        news_id = _safe_component(request.path_params.get("newsId"), "newsId")
        image_name = _safe_component(request.path_params.get("imageName"), "imageName")
    except ValueError as exc:
        return _text(str(exc), status_code=400)

    extension = Path(image_name).suffix.lower()
    media_type = "image/png" if extension == ".png" else "image/jpeg"
    try:
        raw = get_object_store(profile="news").get(("news", news_id, image_name))
    except ObjectNotFoundError:
        return _text("Image not found", status_code=404)
    except ObjectStorageError:
        return _text("Internal server error", status_code=500)
    return Response(content=raw, media_type=media_type, headers={"Cache-Control": GLOBAL_CACHE})


# ---------------------------------------------------------------------------
# UGC binary download
# ---------------------------------------------------------------------------
def _ugc_keys(mission_hash: str, filename: str) -> Tuple[Tuple[str, ...], Optional[Tuple[str, ...]], Tuple[str, ...], bool]:
    extension = Path(filename).suffix.lower()
    is_image = extension in (".jpg", ".jpeg", ".png")
    match = re.match(r"^([0-9]+)_", filename)
    file_id = int(match.group(1)) if match else (1 if is_image else 0)
    primary = (f"{mission_hash}_{file_id:02d}.ugc",)
    fallback = None
    if is_image:
        fallback_id = 1 if file_id == 2 else 2
        fallback = (f"{mission_hash}_{fallback_id:02d}.ugc",)
    default = validate_key((config.UGC_DEFAULT_PHOTO_KEY,))
    validate_key(primary)
    if fallback:
        validate_key(fallback)
    return primary, fallback, default, is_image


def _ugc_authorized(conn, mission_hash: str, user_id: int) -> bool:
    row = conn.execute(
        "SELECT account_id, user_id, is_published FROM ugc_contents WHERE content_id = ?",
        (mission_hash,),
    ).fetchone()
    if row is None:
        # SOURCE_CONFIRMED: upstream does not require a DB row for downloads.
        return True
    if row["is_published"]:
        return True
    return row["account_id"] == user_id or row["user_id"] == user_id


async def ugc_file_download(request: Request) -> Response:
    user = await _native_user(request)
    if not user:
        return _xml(
            cloud_error_xml("Unauthorized", "Invalid session"), status_code=401,
            headers={"Content-Type": "text/xml"},
        )
    try:
        mission_hash = _safe_path_id(
            request.path_params.get("missionHash"), "missionHash", CONTENT_ID_RE
        )
        filename = _safe_component(request.path_params.get("filename"), "filename")
        primary, fallback, default, is_image = _ugc_keys(mission_hash, filename)
    except ValueError as exc:
        return _xml(
            cloud_error_xml("InvalidRequest", str(exc)), status_code=400,
            headers={"Content-Type": "text/xml"},
        )

    with closing(get_db()) as conn:
        if not _ugc_authorized(conn, mission_hash, user["id"]):
            return _xml(
                cloud_error_xml("DoesNotExist", "Content not found"), status_code=404,
                headers={"Content-Type": "text/xml"},
            )

    store = get_object_store(profile="ugc")
    raw: Optional[bytes] = None
    candidates = [("ugc", primary[0])]
    if fallback:
        candidates.append(("ugc", fallback[0]))
    if is_image:
        candidates.append(default)
    for key in candidates:
        try:
            raw = store.get(key)
            break
        except ObjectNotFoundError:
            continue
        except ObjectStorageError:
            return _xml(
                cloud_error_xml("InternalError", "Server error occurred"),
                status_code=500,
                headers={"Content-Type": "text/xml"},
            )
    if raw is None:
        message = "Image not found" if is_image else "Content not found"
        return _xml(
            cloud_error_xml("DoesNotExist", message), status_code=404,
            headers={"Content-Type": "text/xml"},
        )
    media_type = "image/jpeg" if is_image else "application/octet-stream"
    cache = "public, max-age=3600" if is_image else "private, max-age=0"
    return Response(content=raw, media_type=media_type, headers={"Cache-Control": cache})


# ---------------------------------------------------------------------------
# UGC photo upload HMAC
# ---------------------------------------------------------------------------
def _photo_secret() -> bytes:
    if not config.PHOTO_UPLOAD_SECRET:
        raise ObjectStorageConfigurationError(
            "GWSC_PHOTO_UPLOAD_SECRET is required for photo upload tokens"
        )
    return config.PHOTO_UPLOAD_SECRET.encode("utf-8")


def _photo_acl(content_id: str, file_name: str, *, style: str) -> str:
    if style == "cloud":
        return f"/cloud/11/cloudservices/ugc/gta5photo/upload/{content_id}/{file_name}*"
    return f"/ugc/gta5photo/{content_id}/{file_name}*"


def _encode_acl(acl: str) -> str:
    return urllib.parse.quote(acl, safe="").replace("*", "%2a")


def issue_photo_upload_token(
    content_id: str, file_name: str, *, style: str = "short", now: Optional[int] = None
) -> str:
    """Issue a time-limited photo upload token for a locally created content id.

    ``ADAPTED``: upstream uses one HMAC secret for both database and upload
    tokens and signs an unscoped ACL.  This helper uses a dedicated secret and
    an ACL tied to the exact mounted route; callers must still authenticate the
    uploading account.
    """
    if style not in ("cloud", "short"):
        raise ValueError("Unknown photo upload route style")
    content_id = _safe_path_id(content_id, "contentId", CONTENT_ID_RE)
    file_name = _safe_component(file_name, "fileName")
    secret = _photo_secret()
    current = int(time.time()) if now is None else int(now)
    start = max(0, current - 5)
    expiry = current + config.PHOTO_UPLOAD_TOKEN_TTL_SECONDS
    encoded_acl = _encode_acl(_photo_acl(content_id, file_name, style=style))
    signature = hmac.new(
        secret, f"{start}:{expiry}:{encoded_acl}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"__token__=st={start}~exp={expiry}~acl={encoded_acl}~hmac={signature}"


def _verify_photo_upload_token(
    token: str, content_id: str, file_name: str, *, style: str
) -> bool:
    try:
        secret = _photo_secret()
        if token.startswith("__token__="):
            token = token[len("__token__=") :]
        parts = token.split("~")
        if len(parts) != 4:
            return False
        values: Dict[str, str] = {}
        for part in parts:
            key, separator, value = part.partition("=")
            if not separator or key in values:
                return False
            values[key] = value
        start_raw = values.get("st", "")
        expiry_raw = values.get("exp", "")
        acl = values.get("acl", "")
        signature = values.get("hmac", "")
        if not STRICT_INT_RE.fullmatch(start_raw) or not STRICT_INT_RE.fullmatch(expiry_raw):
            return False
        if urllib.parse.unquote(acl) != _photo_acl(content_id, file_name, style=style):
            return False
        start = int(start_raw)
        expiry = int(expiry_raw)
        now = int(time.time())
        if start > now + 10 or expiry < now:
            return False
        expected = hmac.new(
            secret, f"{start}:{expiry}:{acl}".encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
    except (ObjectStorageConfigurationError, ValueError, TypeError):
        return False


def _make_photo_upload_handler(style: str):
    async def upload_photo(request: Request) -> Response:
        photo_user = _photo_user(request)
        if not photo_user:
            return _text("Unauthorized", status_code=401)
        try:
            content_id = _safe_path_id(
                request.path_params.get("contentId"), "contentId", CONTENT_ID_RE
            )
            file_name = _safe_component(request.path_params.get("fileName"), "fileName")
        except ValueError:
            return _text("Invalid upload path", status_code=400)

        token = request.query_params.get("token") or ""
        if not _verify_photo_upload_token(token, content_id, file_name, style=style):
            return _text("Invalid upload token", status_code=401)

        raw = await request.body()
        if not raw:
            return _text("Empty upload body", status_code=400)
        try:
            content_type = _safe_content_type(request.headers.get("content-type"))
        except ValueError:
            return _text("Invalid content type", status_code=400)

        with closing(get_db()) as conn:
            row = conn.execute(
                "SELECT category, account_id, user_id FROM ugc_contents WHERE content_id = ?",
                (content_id,),
            ).fetchone()
            if row is None:
                return _text("Content not found", status_code=404)
            if row["category"] != "gta5photo":
                return _text("Invalid content type", status_code=400)
            if row["account_id"] not in (None, photo_user["id"]) and row[
                "user_id"
            ] not in (None, photo_user["id"]):
                return _text("Forbidden", status_code=403)

        store = get_object_store(profile="ugc")
        try:
            store.put(("ugc", f"{content_id}_00.ugc"), raw, content_type=content_type)
            store.put(("ugc", f"{content_id}_01.ugc"), raw, content_type=content_type)
        except ObjectStorageError:
            return _text("Upload failed", status_code=500)

        with closing(get_db()) as conn:
            with conn:
                conn.execute(
                    "UPDATE ugc_contents SET file_version0 = '0', file_version1 = '0', "
                    "updated_date = ? WHERE content_id = ?",
                    (now_iso(), content_id),
                )
        return Response(
            content=b"",
            status_code=200,
            headers={
                "X-Akamai-RSGShardPath": f"/ugc/gta5photo/{content_id}/0_0.jpg",
                "Cache-Control": "no-store",
            },
        )

    upload_photo.__name__ = f"upload_photo_{style}"
    return upload_photo


router.add_api_route(
    "/cloud/11/cloudservices/members/{platform}/{account_id}/GTA5/saves/mpstats",
    save_file_upload,
    methods=["POST"],
)
router.add_api_route(
    "/cloud/11/cloudservices/members/{platform}/{account_id}/GTA5/saves/mpstats/{saveFileName}",
    save_file_download,
    methods=["GET"],
)
router.add_api_route(
    "/cloud/11/cloudservices/members/np/{scId}/share/gta5/mpchars",
    profile_share_upload,
    methods=["POST"],
)
router.add_api_route(
    "/cloud/11/cloudservices/titles/gta5/ps4/news/news.json",
    news_items,
    methods=["GET"],
)
router.add_api_route(
    "/cloud/11/cloudservices/global/sc/news/{storyKey}/{langFile}",
    news_story,
    methods=["GET"],
)
router.add_api_route(
    "/cloud/11/cloudservices/global/news/image/{newsId}/{imageName}",
    news_image,
    methods=["GET"],
)
router.add_api_route(
    "/cloud/11/cloudservices/ugc/gta5mission/{missionHash}/{filename}",
    ugc_file_download,
    methods=["GET"],
)
router.add_api_route(
    "/cloud/11/cloudservices/ugc/gta5photo/{missionHash}/{filename}",
    ugc_file_download,
    methods=["GET"],
)
router.add_api_route(
    "/cloud/11/cloudservices/ugc/gta5photo/upload/{contentId}/{fileName}",
    _make_photo_upload_handler("cloud"),
    methods=["POST"],
)
router.add_api_route(
    "/ugc/gta5photo/{contentId}/{fileName}",
    _make_photo_upload_handler("short"),
    methods=["POST"],
)
