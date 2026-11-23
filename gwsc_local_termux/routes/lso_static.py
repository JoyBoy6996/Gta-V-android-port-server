"""LSO static/cloud asset parity: tunables, manifests, scripts and redirects.

``SOURCE_CONFIRMED``: upstream LSO serves pre-encrypted tunables, RPF
bgscripts, compatibility XML/JSON files and a static Xbox 360 news list, and
redirects other asset paths to configured third-party hosts.  This port keeps
the exact paths and byte-exact blobs but deliberately drops upstream bugs:
plaintext redirects, unvalidated redirect targets, wrong content types,
CWD-relative reads and RC4-wrapping of binary/image bodies.

Authentication policy: source-confirmed public assets remain readable without
a credential, but a presented ``ros-SessionTicket`` is validated and an
invalid one is rejected.  Crew emblems require an authenticated account and
clan membership (or an open clan).  Every path input is strictly validated.
"""

from __future__ import annotations

import io
import logging
import re
import urllib.parse
from contextlib import closing
from typing import Optional

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from .. import config
from ..auth import authenticate_native_request
from ..database import get_db
from ..native_requests import require_user, session_ticket_header
from ..object_storage import (
    ObjectNotFoundError,
    ObjectStorageError,
    get_object_store,
    validate_key,
)
from ..static_assets import ASSETS, StaticAssetError, load_asset

router = APIRouter(tags=["lso-static"])
log = logging.getLogger("gwsc.lso_static")

# SOURCE_CONFIRMED: upstream's global asset cache header.
GLOBAL_CACHE = "public, max-age=0, immutable"
HSTS = "max-age=31536000; includeSubDomains"

STRICT_INT_RE = re.compile(r"^[0-9]{1,10}$")
IMAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_EMBLEM_SOURCE_BYTES = 8 * 1024 * 1024
MAX_CLAN_ID = 2_147_483_647

# SOURCE_CONFIRMED: the two hard-coded appnpps4.json payloads.  They are
# developer placeholders upstream, not credentials or generated state.
DOG_APP_JSON = {
    "version": 1,
    "ownerID": "Jorby",
    "blockName": {"nestedBlockName": {"key": "value"}, "simpleKey": "simpleValue"},
}
CAR_APP_JSON = {
    "version": 1,
    "ownerID": "Jorby",
    "blockName": {
        "companion_waypoints": {"active_route": "value", "last_position": "coordinates"},
        "dog_status": {"happiness": "value", "training_level": "value"},
    },
}


def _headers(request: Request, *, cache: str = GLOBAL_CACHE) -> dict[str, str]:
    headers = {"Cache-Control": cache}
    # Use only the actual request scheme.  Arbitrary X-Forwarded-Proto values
    # must not be able to force HSTS on clients.
    if request.url.scheme == "https":
        headers["Strict-Transport-Security"] = HSTS
    return headers


def _json_error(
    request: Request, status_code: int, error: str, message: str
) -> JSONResponse:
    return JSONResponse(
        {"error": error, "message": message},
        status_code=status_code,
        headers=_headers(request),
    )


def _text_error(request: Request, status_code: int, message: str) -> Response:
    return Response(
        content=message,
        status_code=status_code,
        media_type="text/plain; charset=utf-8",
        headers=_headers(request),
    )


def _validate_optional_ticket(request: Request) -> Optional[Response]:
    """Public read, but a supplied session ticket must be valid and active."""
    ticket = session_ticket_header(request)
    if not ticket:
        return None
    user = authenticate_native_request(session_ticket=ticket)
    if not user or user.get("status") != "active":
        return _json_error(request, 401, "unauthorized", "Invalid session ticket")
    return None


def _make_asset_handler(asset_name: str):
    async def asset_handler(request: Request) -> Response:
        denied = _validate_optional_ticket(request)
        if denied is not None:
            return denied
        try:
            raw = load_asset(asset_name)
        except StaticAssetError:
            log.error("Static asset verification failed for %s", asset_name)
            return _json_error(
                request, 500, "asset_unavailable", "Static asset is unavailable"
            )
        return Response(
            content=raw,
            media_type=ASSETS[asset_name].media_type,
            headers=_headers(request),
        )

    asset_handler.__name__ = f"static_asset_{asset_name}"
    return asset_handler


# SOURCE_CONFIRMED: exact upstream paths and the vendored blob behind each.
ASSET_ROUTES = {
    "/cloud/11/cloudservices/titles/gta5/ps4/0x1a098062.json": "ps4_tunables",
    "/titles/gta5/ps2/0x1a098062.json": "ps2_tunables",
    "/cloud/11/cloudservices/titles/gta5/ps5/0x1a098062.json": "ps5_tunables",
    "/cloud/11/cloudservices/titles/gta5/xbox360/news/news.json": "xbox360_news",
    "/titles/gta5/xbox360/check.json": "check_json",
    "/titles/gta5/xbox360/extraContent/ExtraContentManifest.xml": "extra_content_manifest",
    "/cloud/11/cloudservices/titles/gta5/ps5/legal/version/version_num.xml": "version_num",
    "/cloud/11/cloudservices/titles/gta5/ps4/legal/version/version_num.xml": "version_num",
    "/titles/gta5/xbox360/bgscripts/bg_900_0.rpf": "xbox360_bgscript",
    "/cloud/11/cloudservices/titles/gta5/ps5/bgscripts/bg_ng_171_8.rpf": "ps5_bgscript",
    "/titles/gta5/ps4/store/SCEA/en/commerceData.xml": "ps4_commerce",
    "/titles/gta5/xbox360/store/en/commerceData.xml": "xbox360_commerce",
}

for _path, _asset_name in ASSET_ROUTES.items():
    router.add_api_route(_path, _make_asset_handler(_asset_name), methods=["GET", "HEAD"])


async def dynamic_tunables(request: Request) -> Response:
    """PS3 tunables are intentionally not generated.

    ``DELIBERATELY OMITTED``: upstream AES-encrypts a JSON blob with a
    hardcoded key and a mutable shared defaults object.  No Android capture
    confirms that contract, and the key must not be copied.  The route exists
    so the path returns a stable error instead of falling through to a greedy
    native alias.
    """
    denied = _validate_optional_ticket(request)
    if denied is not None:
        return denied
    return _json_error(
        request,
        501,
        "unsupported",
        "Dynamic PS3 tunables require a source-confirmed crypto contract",
    )


router.add_api_route(
    "/titles/gta5/ps3/0x1a098062.json", dynamic_tunables, methods=["GET", "HEAD"]
)


# ---------------------------------------------------------------------------
# Redirects
# ---------------------------------------------------------------------------
def _target(name: str) -> str:
    if name == "prod":
        return config.STATIC_REDIRECT_PROD_CLOUD
    if name == "tunables":
        return config.STATIC_REDIRECT_TUNABLES
    if name == "ros":
        return config.STATIC_REDIRECT_ROS
    return ""


def _redirect(
    request: Request, target_name: str, suffix: str
) -> Response:
    """Create a validated 302 to an explicitly configured HTTPS base URL."""
    base = _target(target_name)
    if not base:
        return _json_error(
            request,
            404,
            "redirect_not_configured",
            "Static redirect target is not configured",
        )
    parsed = urllib.parse.urlsplit(base)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not host
        or host not in config.STATIC_REDIRECT_ALLOWED_HOSTS
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        # Never echo the configured value: it may contain a credential.
        return _json_error(
            request,
            500,
            "redirect_misconfigured",
            "Static redirect target is not allowed by the host allowlist",
        )
    location = urllib.parse.urlunsplit(
        ("https", parsed.netloc, "/" + suffix.lstrip("/"), "", "")
    )
    return Response(status_code=302, headers={**_headers(request), "Location": location})


def _make_redirect_handler(target_name: str, suffix: str):
    async def redirect_handler(request: Request) -> Response:
        denied = _validate_optional_ticket(request)
        if denied is not None:
            return denied
        return _redirect(request, target_name, suffix)

    redirect_handler.__name__ = f"static_redirect_{target_name}_{abs(hash(suffix)) % 10000}"
    return redirect_handler


REDIRECT_ROUTES = {
    "/cloud/11/cloudservices/titles/gta5/pcros/0x1a098062.json": (
        "prod",
        "titles/gta5/pcros/0x1a098062.json",
    ),
    "/cloud/11/cloudservices/titles/gta5/xbox360/0x1a098062.json": (
        "tunables",
        "titles/gta5/ps3/0x1a098062.json",
    ),
    "/cloud/11/cloudservices/titles/gta5/xboxone/check.json": (
        "prod",
        "titles/gta5/xboxone/check.json",
    ),
    "/cloud/11/cloudservices/titles/gta5/ps4/check.json": (
        "prod",
        "titles/gta5/ps4/check.json",
    ),
    "/cloud/11/cloudservices/titles/gta5/xbox360/check.json": (
        "tunables",
        "titles/gta5/xbox360/check.json",
    ),
    "/cloud/11/cloudservices/titles/gta5/xboxone/extraContent/ExtraContentManifest.xml": (
        "prod",
        "titles/gta5/xboxone/extraContent/ExtraContentManifest.xml",
    ),
    "/cloud/11/cloudservices/titles/gta5/ps4/extraContent/ExtraContentManifest.xml": (
        "prod",
        "titles/gta5/ps4/extraContent/ExtraContentManifest.xml",
    ),
    "/cloud/11/cloudservices/titles/gta5/xbox360/extraContent/ExtraContentManifest.xml": (
        "tunables",
        "titles/gta5/xbox360/extraContent/ExtraContentManifest.xml",
    ),
    "/cloud/11/cloudservices/titles/gta5/ps4/bgscripts/bg_902_0.rpf": (
        "prod",
        "cloud/11/cloudservices/titles/gta5/xboxone/bgscripts/bg_ng_2843_0.rpf",
    ),
    "/cloud/11/cloudservices/titles/gta5/xbox360/bgscripts/bg_900_0.rpf": (
        "tunables",
        "titles/gta5/xbox360/bgscripts/bg_900_0.rpf",
    ),
    "/cloud/11/cloudservices/titles/gta5/xbox360/store/en/commerceData.xml": (
        "tunables",
        "titles/gta5/xbox360/store/en/commerceData.xml",
    ),
    "/cloud/11/cloudservices/titles/gta5/ps4/store/commerceData.xml": (
        "ros",
        "titles/gta5/ps4/store/SCEA/en/commerceData.xml",
    ),
}

for _path, (_target_name, _suffix) in REDIRECT_ROUTES.items():
    router.add_api_route(
        _path, _make_redirect_handler(_target_name, _suffix), methods=["GET", "HEAD"]
    )


async def xboxone_tunables(request: Request) -> Response:
    """``ADAPTED``: upstream redirects via ROS_HOST; use a local relative 302.

    A relative Location cannot be poisoned by a configured or forwarded host
    and points at the byte-exact PS2 tunables route above.
    """
    denied = _validate_optional_ticket(request)
    if denied is not None:
        return denied
    return Response(
        status_code=302,
        headers={
            **_headers(request),
            "Location": "/titles/gta5/ps2/0x1a098062.json",
        },
    )


router.add_api_route(
    "/cloud/11/cloudservices/titles/gta5/xboxone/0x1a098062.json",
    xboxone_tunables,
    methods=["GET", "HEAD"],
)


def _make_image_redirect_handler(platform: str):
    async def image_redirect(request: Request) -> Response:
        denied = _validate_optional_ticket(request)
        if denied is not None:
            return denied
        image_name = request.path_params.get("imageName", "")
        if not IMAGE_NAME_RE.fullmatch(image_name) or ".." in image_name:
            return _json_error(request, 400, "invalid_request", "Invalid image name")
        suffix = (
            f"titles/gta5/{platform}/store/images/editions/"
            f"{urllib.parse.quote(image_name, safe='')}"
        )
        return _redirect(request, "prod", suffix)

    image_redirect.__name__ = f"static_image_redirect_{platform}"
    return image_redirect


for _platform in ("xbox360", "ps4"):
    router.add_api_route(
        f"/cloud/11/cloudservices/titles/gta5/{_platform}/store/images/editions/{{imageName}}",
        _make_image_redirect_handler(_platform),
        methods=["GET", "HEAD"],
    )


# ---------------------------------------------------------------------------
# Source-confirmed developer placeholder JSON
# ---------------------------------------------------------------------------
def _make_json_handler(payload: dict):
    async def json_handler(request: Request) -> Response:
        denied = _validate_optional_ticket(request)
        if denied is not None:
            return denied
        return JSONResponse(content=payload, headers=_headers(request))

    return json_handler


router.add_api_route(
    "/cloud/11/cloudservices/members/sc/1/GTA5/dog/appnpps4.json",
    _make_json_handler(DOG_APP_JSON),
    methods=["GET", "HEAD"],
)
router.add_api_route(
    "/cloud/11/cloudservices/members/sc/1/GTA5/car/appnpps4.json",
    _make_json_handler(CAR_APP_JSON),
    methods=["GET", "HEAD"],
)


# ---------------------------------------------------------------------------
# Crew emblems
# ---------------------------------------------------------------------------
def _emblem_key(raw_key: str):
    """Restrict DB-provided emblem keys to a safe emblems/ prefix."""
    parts = [part for part in raw_key.strip().lstrip("/").split("/") if part]
    if len(parts) < 2 or parts[0] != "emblems":
        raise ValueError("Invalid emblem key")
    return validate_key(parts)


def _image_media_type(raw: bytes) -> Optional[str]:
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return None


def _resize_emblem(raw: bytes, size: int) -> tuple[bytes, str]:
    """Resize with Pillow when available; otherwise serve verified bytes.

    ``ADAPTED``: upstream uses sharp to produce JPEG.  Pillow is optional in
    this project, so legacy JPEG/PNG source bytes are served unchanged when it
    is absent.  The .dds URL suffix is a client contract, not a guarantee about
    stored bytes; unknown formats are rejected rather than mislabelled.
    """
    media_type = _image_media_type(raw)
    if media_type is None:
        raise ValueError("Unsupported emblem format")
    try:
        from PIL import Image, ImageOps  # type: ignore[import-not-found]
    except ImportError:
        return raw, media_type
    Image.MAX_IMAGE_PIXELS = 20_000_000
    with Image.open(io.BytesIO(raw)) as source:
        source = ImageOps.exif_transpose(source).convert("RGB")
        fitted = ImageOps.fit(source, (size, size), method=Image.Resampling.LANCZOS)
        output = io.BytesIO()
        fitted.save(output, format="JPEG", quality=90, dpi=(72, 72))
    return output.getvalue(), "image/jpeg"


async def crew_emblem(request: Request) -> Response:
    user = require_user(request, dict(request.query_params))
    if not user:
        return _text_error(request, 401, "Unauthorized")

    raw_clan_id = request.path_params.get("clanId", "")
    raw_size = request.path_params.get("size", "")
    if not STRICT_INT_RE.fullmatch(raw_clan_id) or int(raw_clan_id) > MAX_CLAN_ID:
        return _text_error(request, 400, "Invalid clan id")
    if not STRICT_INT_RE.fullmatch(raw_size) or not 1 <= int(raw_size) <= 1024:
        return _text_error(request, 400, "Invalid emblem size")
    clan_id = int(raw_clan_id)
    size = int(raw_size)

    with closing(get_db()) as conn:
        clan = conn.execute(
            "SELECT emblem_path, is_open_clan FROM clans WHERE id = ?", (clan_id,)
        ).fetchone()
        if clan is None or not clan["emblem_path"]:
            return _text_error(request, 404, "Emblem not found")
        membership = conn.execute(
            "SELECT 1 FROM clan_memberships WHERE clan_id = ? AND user_id = ?",
            (clan_id, user["id"]),
        ).fetchone()
    if membership is None and not clan["is_open_clan"]:
        return _text_error(request, 403, "Forbidden")

    try:
        key = _emblem_key(clan["emblem_path"])
    except ValueError:
        return _text_error(request, 404, "Emblem not found")
    try:
        raw = get_object_store().get(key)
    except ObjectNotFoundError:
        return _text_error(request, 404, "Emblem not found")
    except ObjectStorageError:
        return _text_error(request, 500, "Internal server error")
    if len(raw) > MAX_EMBLEM_SOURCE_BYTES:
        return _text_error(request, 413, "Emblem image is too large")
    try:
        image, media_type = _resize_emblem(raw, size)
    except ValueError:
        return _text_error(request, 415, "Unsupported emblem format")
    except Exception:
        log.exception("Could not process crew emblem")
        return _text_error(request, 500, "Internal server error")
    return Response(content=image, media_type=media_type, headers=_headers(request))


router.add_api_route(
    "/cloud/11/cloudservices/crews/sc/{clanId}/publish/emblem/emblem_{size}.dds",
    crew_emblem,
    methods=["GET", "HEAD"],
)
