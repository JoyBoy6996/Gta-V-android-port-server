"""LSO UGC content lifecycle parity: create, update, publish, delete and query.

``SOURCE_CONFIRMED`` from the upstream LSO checkout:

- the five routes live under ``/gta5/11/gameservices/ugc.asmx``;
- Create/Update use the ``&data=`` binary-frame decoder and JSON form fields
  ``paramsJson`` / ``updateJson``;
- content ids are 16 random bytes in base64url form, with root content id equal
  to the first id;
- response envelopes use the ``CreateContentResponse``, ``CreateContent``,
  ``UpdateContentResponse``, ``PublishResponse`` and ``QueryContent`` shapes;
- two hardcoded heist ids short-circuit QueryContent to byte-exact static XML.

``ADAPTED`` for this server: RC4 transport and upstream's per-platform key
blobs are not ported.  The caller comes from the existing account-bound native
credential, never from the request body.  Update/Publish/SetDeleted strictly
enforce ownership (upstream's SetDeleted did not), unpublished content is not
exposed to anonymous queries, upload failures are errors instead of concealed
success, and all binary/XML/JSON inputs are bounded and validated.  Android
acceptance remains ``UNVERIFIED``.
"""

from __future__ import annotations

import json
import re
import secrets
import struct
import urllib.parse
from contextlib import closing
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from .. import config
from ..database import get_db
from ..native_requests import (
    field,
    request_values,
    require_user,
    session_ticket_header,
)
from ..object_storage import (
    ObjectStorageConfigurationError,
    ObjectStorageError,
    get_object_store,
    validate_key,
)
from ..static_assets import StaticAssetError, load_asset
from ..time_utils import now_iso, parse_iso
from ..xml_protocol import lso_response_xml
from .lso_cloud import issue_photo_upload_token

router = APIRouter(tags=["lso-ugc"])

TEXT_XML = "text/xml; charset=utf-8"
CONTENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
QUOTE_KEYS_RE = re.compile(r"([{,]\s*)([A-Za-z0-9_]+)(\s*:)")
SINGLE_QUOTE_RE = re.compile(r":\s*'([^']*)'")
TRAILING_VERSION_RE = re.compile(r"_\d{2}$")

MAX_UGC_FILES = 3
MAX_JSON_LENGTH = 65_536
MAX_CONTENT_NAME = 256
MAX_DESCRIPTION = 4_096
MAX_DATA_JSON = 1_048_576
MAX_LANGUAGE = 16
MAX_CONTENT_IDS = 100
MAX_QUERY_COUNT = 100
LEGACY_PLATFORMS = frozenset({"PS3", "XBOX360"})

HEIST1_TRIGGER = "QS6WYcjJFk2YxqYDMN8mjQ"
HEIST2_TRIGGER = "hK5OgJk1BkinXGGXghhTMg"

UPLOAD_COLUMNS = (
    "content_id",
    "root_content_id",
    "description",
    "data",
    "category",
    "file_version0",
    "file_version1",
    "file_version2",
    "language",
    "username",
    "name",
    "account_id",
    "rockstar_id",
    "user_id",
    "created_date",
    "published_date",
    "updated_date",
    "version",
    "is_verified",
    "is_legacy",
    "is_published",
)


def _xml(content: str, status_code: int = 200) -> Response:
    return Response(content=content, status_code=status_code, media_type=TEXT_XML)


def _text(message: str, status_code: int = 200) -> Response:
    return Response(
        content=message, status_code=status_code, media_type="text/plain; charset=utf-8"
    )


def _json_error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse({"message": message}, status_code=status_code)


def _safe_content_id(value: Any) -> str:
    text = "" if value is None else str(value)
    if not CONTENT_ID_RE.fullmatch(text):
        raise ValueError("Invalid content id")
    return text


def _new_content_id() -> str:
    """Generate the source-confirmed 16-byte base64url id as a valid key part."""
    for _ in range(16):
        candidate = secrets.token_urlsafe(16)
        if candidate[0].isalnum():
            return candidate
    raise RuntimeError("Could not generate a valid UGC content id")


def _text_param(params: Dict[str, Any], name: str, limit: int) -> str:
    value = params.get(name)
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"Invalid {name}")
    return value


def _publish_flag(params: Dict[str, Any]) -> bool:
    value = params.get("Publish")
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ValueError("Invalid Publish")
    return value


def _resolve_content_type(raw: Any) -> Optional[str]:
    normalized = str(raw or "gta5mission").strip().lower()
    if normalized in ("gta5mission", "1"):
        return "gta5mission"
    if normalized in ("gta5photo", "photo", "4"):
        return "gta5photo"
    return None


def _unix_seconds(value: Optional[str]) -> str:
    parsed = parse_iso(value)
    return str(int(parsed.timestamp())) if parsed else "0"


def _platform(values: Dict[str, Any], request: Request) -> str:
    return str(
        field(values, "Platform", "platform") or request.headers.get("platform") or ""
    ).strip().upper()


def _decode_ugc_binary(raw: bytes) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Parse the source-confirmed UGC ``&data=`` frame format with bounds."""
    marker = raw.find(b"&data=")
    if marker < 0:
        raise ValueError("Invalid request format")
    if marker > MAX_JSON_LENGTH * 4:
        raise ValueError("Form data is too large")
    try:
        decoded_form = raw[:marker].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Invalid request format") from exc
    parsed = urllib.parse.parse_qs(decoded_form, keep_blank_values=True)
    form: Dict[str, Any] = {key: values[-1] for key, values in parsed.items()}

    for key in ("paramsJson", "updateJson"):
        if key not in form:
            continue
        value = form[key]
        if not isinstance(value, str) or len(value) > MAX_JSON_LENGTH:
            raise ValueError("Invalid request format")
        try:
            decoded = json.loads(value)
        except ValueError as exc:
            raise ValueError("Invalid request format") from exc
        if not isinstance(decoded, dict):
            raise ValueError("Invalid request format")
        form[key] = decoded

    files: List[Dict[str, Any]] = []
    offset = marker + len(b"&data=")
    while offset < len(raw):
        if len(raw) - offset < 8:
            raise ValueError("Invalid request format")
        file_size, file_id = struct.unpack(">ii", raw[offset : offset + 8])
        offset += 8
        if (
            file_size < 0
            or file_size > config.OBJECT_MAX_BYTES
            or offset + file_size > len(raw)
        ):
            raise ValueError("Invalid request format")
        files.append(
            {
                "id": file_id,
                "size": file_size,
                "data": raw[offset : offset + file_size],
            }
        )
        offset += file_size
        if len(files) > MAX_UGC_FILES:
            raise ValueError("Too many files")
    return form, files


def _auth_values(request: Request, form: Dict[str, Any]) -> Dict[str, Any]:
    values: Dict[str, Any] = dict(request.query_params)
    values.update(
        {key: value for key, value in form.items() if isinstance(value, str)}
    )
    return values


def _credential_present(request: Request, values: Dict[str, Any]) -> bool:
    return bool(
        session_ticket_header(request)
        or request.headers.get("authorization")
        or request.headers.get("x-gwsc-token")
        or request.query_params.get("scAuthToken")
        or request.query_params.get("token")
        or field(values, "ticket", "scAuthToken", "authToken", "token", "sessionTicket")
    )


def _store_files(content_id: str, files: List[Dict[str, Any]], category: str) -> None:
    if not files:
        return
    store = get_object_store(profile="ugc")
    for index, item in enumerate(files[:MAX_UGC_FILES]):
        suffix = f"{index:02d}"
        key = validate_key(("ugc", f"{content_id}_{suffix}.ugc"))
        media_type = (
            "application/octet-stream"
            if category == "gta5mission" and index == 0
            else "image/jpeg"
        )
        store.put(key, item["data"], content_type=media_type)


def _file_versions(files: List[Dict[str, Any]], category: str) -> Tuple[str, str, str]:
    if category == "gta5photo":
        return (
            str(files[0]["id"]) if len(files) > 0 else "-1",
            str(files[1]["id"]) if len(files) > 1 else "-1",
            "-1",
        )
    return (
        str(files[0]["id"]) if len(files) > 0 else "1",
        str(files[1]["id"]) if len(files) > 1 else "0",
        str(files[2]["id"]) if len(files) > 2 else "0",
    )


def _create_mission_xml(row: Dict[str, Any]) -> str:
    attributes = {
        "ci": row["content_id"],
        "cd": _unix_seconds(row["created_date"]),
        "ud": _unix_seconds(row["updated_date"]),
        "u": str(row["rockstar_id"] or 0),
        "v": str(row["version"] or 1),
        "n": row["name"],
        "rci": row["root_content_id"],
        "f0": str(row["file_version0"] or "-1"),
        "f1": str(row["file_version1"] or "-1"),
        "f2": str(row["file_version2"] or "0"),
    }
    result = ET.Element("Result", attrib=attributes)
    if row["data"]:
        ET.SubElement(result, "da").text = row["data"]
    return lso_response_xml([result], xmlns="CreateContentResponse")


def _cdn_base_url() -> str:
    base = config.UGC_PUBLIC_BASE_URL.strip()
    if not base and config.PUBLIC_HOST:
        base = f"https://{config.PUBLIC_HOST}"
    if not base:
        return ""
    parsed = urllib.parse.urlsplit(base)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ObjectStorageConfigurationError("Invalid UGC public base URL")
    return base.rstrip("/")


def _photo_metadata(content_id: str, base_data_json: Optional[str]) -> str:
    metadata: Dict[str, Any] = {}
    if base_data_json:
        try:
            parsed = json.loads(base_data_json)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            metadata.update(parsed)
    metadata.update(
        {
            "cdndir": f"/ugc/gta5photo/{content_id}/",
            "cdnfn": "0_0.jpg",
            "cdnurl": _cdn_base_url(),
            "cdnat": issue_photo_upload_token(content_id, "0_0.jpg", style="short"),
        }
    )
    return json.dumps(metadata, separators=(",", ":"))


def _create_photo_xml(row: Dict[str, Any], metadata_json: str) -> str:
    attributes = {
        "ci": row["content_id"],
        "cd": _unix_seconds(row["created_date"]),
        "f1": "-1",
        "f2": "-1",
        "n": row["name"],
        "rci": row["root_content_id"],
        "ud": _unix_seconds(row["updated_date"]),
        "u": str(row["rockstar_id"] or 0),
        "v": str(row["version"] or 1),
    }
    result = ET.Element("Result", attrib=attributes)
    ET.SubElement(result, "da").text = metadata_json
    return lso_response_xml([result], xmlns="CreateContent")


def _small_result_xml(xmlns: str, fields: List[Tuple[str, Any]]) -> str:
    result = ET.Element("Result")
    for name, value in fields:
        if value is None:
            continue
        ET.SubElement(result, name).text = str(value)
    return lso_response_xml([result], xmlns=xmlns)


def _is_owner(row: Any, user: Dict[str, Any]) -> bool:
    return row["account_id"] == user["id"] or row["user_id"] == user["id"] or (
        row["rockstar_id"] is not None and row["rockstar_id"] == user["rockstar_id"]
    )


def _visibility_clause(
    user: Optional[Dict[str, Any]], *, alias: str = ""
) -> Tuple[str, List[Any]]:
    """Published-or-owner filter, optionally qualified for a joined table."""
    prefix = f"{alias}." if alias else ""
    if user is None:
        return f"{prefix}is_published = 1", []
    return (
        f"({prefix}is_published = 1 OR {prefix}account_id = ? "
        f"OR {prefix}user_id = ? OR {prefix}rockstar_id = ?)"
    ), [user["id"], user["id"], user["rockstar_id"]]


async def create_content(request: Request) -> Response:
    raw = await request.body()
    try:
        form, files = _decode_ugc_binary(raw)
        auth_values = _auth_values(request, form)
        user = require_user(request, auth_values)
        if not user:
            return _json_error(401, "Unauthorized")
        params = form.get("paramsJson") or {}
        if not isinstance(params, dict):
            raise ValueError("Invalid request format")
        category = _resolve_content_type(field(form, "contentType", "content_type"))
        if category is None:
            return _text("Unsupported content type", status_code=400)
        name = _text_param(params, "ContentName", MAX_CONTENT_NAME) or (
            "Untitled Mission" if category == "gta5mission" else "Untitled Photo"
        )
        description = _text_param(params, "Description", MAX_DESCRIPTION)
        data = _text_param(params, "DataJson", MAX_DATA_JSON)
        language = _text_param(params, "Language", MAX_LANGUAGE) or "en"
        publish = _publish_flag(params)
        content_id = _new_content_id()
        platform = _platform(auth_values, request)
        is_legacy = platform in LEGACY_PLATFORMS
        versions = _file_versions(files, category)
        timestamp = now_iso()
    except ValueError as exc:
        return _text(str(exc) or "Invalid request format", status_code=400)

    row = {
        "content_id": content_id,
        "root_content_id": content_id,
        "description": description,
        "data": data,
        "category": category,
        "file_version0": versions[0],
        "file_version1": versions[1],
        "file_version2": versions[2],
        "language": language,
        "username": user["nickname"],
        "name": name,
        "account_id": user["id"],
        "rockstar_id": user["rockstar_id"],
        "user_id": user["id"],
        "created_date": timestamp,
        "published_date": timestamp if publish else None,
        "updated_date": timestamp,
        "version": 1,
        "is_verified": 0,
        "is_legacy": int(is_legacy),
        "is_published": int(publish),
    }
    placeholders = ", ".join("?" for _ in UPLOAD_COLUMNS)
    with closing(get_db()) as conn:
        with conn:
            conn.execute(
                f"INSERT INTO ugc_contents ({', '.join(UPLOAD_COLUMNS)}) "
                f"VALUES ({placeholders})",
                tuple(row[column] for column in UPLOAD_COLUMNS),
            )
    try:
        _store_files(content_id, files, category)
    except ObjectStorageError:
        return _text("Upload failed", status_code=500)

    if category == "gta5photo":
        try:
            metadata_json = _photo_metadata(content_id, data or None)
        except (ObjectStorageConfigurationError, ValueError):
            return _json_error(500, "Photo upload is not configured")
        return _xml(_create_photo_xml(row, metadata_json))
    return _xml(_create_mission_xml(row))


async def update_content(request: Request) -> Response:
    raw = await request.body()
    try:
        form, files = _decode_ugc_binary(raw)
        auth_values = _auth_values(request, form)
        user = require_user(request, auth_values)
        if not user:
            return _json_error(401, "Unauthorized")
        content_id = _safe_content_id(field(form, "contentId"))
        update_json = form.get("updateJson") or {}
        if not isinstance(update_json, dict):
            raise ValueError("Invalid request format")
    except ValueError as exc:
        return _text(str(exc) or "Invalid request format", status_code=400)

    with closing(get_db()) as conn:
        existing = conn.execute(
            "SELECT * FROM ugc_contents WHERE content_id = ?", (content_id,)
        ).fetchone()
        if existing is None:
            return _json_error(404, "Content not found")
        if not _is_owner(existing, user):
            return _json_error(403, "Forbidden - you don't own this content")

        try:
            name = _text_param(update_json, "ContentName", MAX_CONTENT_NAME)
            data = _text_param(update_json, "DataJson", MAX_DATA_JSON)
            description = _text_param(update_json, "Description", MAX_DESCRIPTION)
            language = _text_param(update_json, "Language", MAX_LANGUAGE)
            publish = _publish_flag(update_json)
        except ValueError as exc:
            return _text(str(exc), status_code=400)

        updates: Dict[str, Any] = {
            "updated_date": now_iso(),
            "version": int(existing["version"] or 1) + 1,
        }
        if name:
            updates["name"] = name
        if data:
            updates["data"] = data
        if description:
            updates["description"] = description
        if language:
            updates["language"] = language
        if publish and not existing["published_date"]:
            updates["published_date"] = updates["updated_date"]
            updates["is_published"] = 1
        if files:
            versions = _file_versions(files, existing["category"])
            updates["file_version0"] = versions[0]
            updates["file_version1"] = versions[1]
            updates["file_version2"] = versions[2]

        assignments = ", ".join(f"{column} = ?" for column in updates)
        with conn:
            conn.execute(
                f"UPDATE ugc_contents SET {assignments} WHERE content_id = ?",
                (*updates.values(), content_id),
            )
        row = conn.execute(
            "SELECT * FROM ugc_contents WHERE content_id = ?", (content_id,)
        ).fetchone()

    try:
        _store_files(TRAILING_VERSION_RE.sub("", content_id), files, row["category"])
    except ObjectStorageError:
        return _text("Upload failed", status_code=500)

    fields: List[Tuple[str, Any]] = [
        ("ci", row["content_id"]),
        ("n", row["name"]),
        ("ud", _unix_seconds(row["updated_date"])),
    ]
    if row["published_date"]:
        fields.append(("pd", _unix_seconds(row["published_date"])))
    return _xml(_small_result_xml("UpdateContentResponse", fields))


async def publish_content(request: Request) -> Response:
    values = dict(await request_values(request))
    user = require_user(request, values)
    if not user:
        return _json_error(401, "Unauthorized")
    try:
        content_id = _safe_content_id(field(values, "contentId"))
    except ValueError as exc:
        return _text(str(exc), status_code=400)

    with closing(get_db()) as conn:
        row = conn.execute(
            "SELECT * FROM ugc_contents WHERE content_id = ?", (content_id,)
        ).fetchone()
        if row is None:
            return _json_error(404, "Content not found")
        if not _is_owner(row, user):
            return _json_error(403, "Forbidden - you don't own this content")
        timestamp = now_iso()
        with conn:
            conn.execute(
                "UPDATE ugc_contents SET is_published = 0 "
                "WHERE root_content_id = ? AND content_id != ?",
                (row["root_content_id"], content_id),
            )
            conn.execute(
                "UPDATE ugc_contents SET is_published = 1, published_date = ? "
                "WHERE content_id = ?",
                (timestamp, content_id),
            )
        published = conn.execute(
            "SELECT * FROM ugc_contents WHERE content_id = ?", (content_id,)
        ).fetchone()

    fields = [
        ("ci", published["content_id"]),
        ("rci", published["root_content_id"]),
        ("n", published["name"]),
        ("pd", _unix_seconds(published["published_date"])),
    ]
    return _xml(_small_result_xml("PublishResponse", fields))


SET_DELETED_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    "<Response>\n"
    "    <Status>1</Status>\n"
    "</Response>"
)


async def set_deleted(request: Request) -> Response:
    values = dict(await request_values(request))
    user = require_user(request, values)
    if not user:
        return _json_error(401, "Unauthorized")
    try:
        content_id = _safe_content_id(field(values, "contentId"))
    except ValueError as exc:
        return _text(str(exc), status_code=400)

    with closing(get_db()) as conn:
        row = conn.execute(
            "SELECT * FROM ugc_contents WHERE content_id = ?", (content_id,)
        ).fetchone()
        if row is None:
            return _json_error(404, "Content not found")
        if not _is_owner(row, user):
            return _json_error(403, "Forbidden - you don't own this content")
        with conn:
            conn.execute(
                "UPDATE ugc_contents SET is_published = 0, published_date = NULL, "
                "updated_date = ? WHERE content_id = ?",
                (now_iso(), content_id),
            )
    return _xml(SET_DELETED_XML)


def _parse_query_params(raw: Any) -> Dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if not isinstance(raw, str) or len(raw) > MAX_JSON_LENGTH:
        raise ValueError("Invalid query parameters")
    try:
        parsed = json.loads(raw)
    except ValueError:
        normalized = SINGLE_QUOTE_RE.sub(r': "\1"', QUOTE_KEYS_RE.sub(r'\1"\2"\3', raw))
        try:
            parsed = json.loads(normalized)
        except ValueError as exc:
            raise ValueError("Invalid query parameters") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Invalid query parameters")
    return parsed


def _paging(values: Dict[str, Any]) -> Tuple[int, int]:
    def parse(name: str, default: int) -> int:
        raw = field(values, name)
        if raw is None or raw == "":
            return default
        try:
            number = int(str(raw))
        except ValueError as exc:
            raise ValueError(f"Invalid {name}") from exc
        if number < 0 or number > MAX_QUERY_COUNT:
            raise ValueError(f"Invalid {name}")
        return number

    return parse("offset", 0), parse("count", 31)


def _query_row_xml(row: Any) -> ET.Element:
    item = ET.Element("r", attrib={"c": str(row["content_id"])})
    attributes: Dict[str, str] = {
        "ca": str(row["category"]),
        "f0": str(row["file_version0"]),
        "f1": str(row["file_version1"]),
        "f2": str(row["file_version2"]),
        "n": str(row["name"]),
        "rci": str(row["root_content_id"]),
        "un": str(row["username"]),
        "r": str(row["rockstar_id"]),
        "a": str(row["account_id"]),
        "u": str(row["user_id"]),
        "l": str(row["language"]),
        "cd": str(row["created_date"]),
        "pd": str(row["published_date"]),
        "ud": str(row["updated_date"]),
        "v": str(row["version"]),
    }
    for key in ("f0", "f1", "f2", "pd"):
        if attributes[key] in ("-1", "None", ""):
            attributes.pop(key, None)
    if row["is_verified"]:
        attributes["vci"] = "true"
    detail = ET.SubElement(item, "m", attrib=attributes)
    if row["data"]:
        ET.SubElement(detail, "da").text = row["data"]
    if row["description"]:
        ET.SubElement(detail, "de").text = row["description"]
    return item


def _query_xml(
    rows: List[Any], total: Optional[int] = None, *, status: int = 1
) -> str:
    result = ET.Element(
        "Result",
        attrib={
            "Count": str(len(rows)),
            "Total": str(len(rows) if total is None else total),
            "Hash": "0",
        },
    )
    for row in rows:
        result.append(_query_row_xml(row))
    return lso_response_xml([result], xmlns="QueryContent", status=status)


async def query_content(request: Request) -> Response:
    values = dict(await request_values(request))
    user = require_user(request, values)
    if not user and _credential_present(request, values):
        return _json_error(401, "Unauthorized")

    query_name = field(values, "queryName")
    if not query_name:
        return _text("Invalid queryName", status_code=400)
    try:
        query_params = _parse_query_params(field(values, "queryParams"))
        offset, count = _paging(values)
    except ValueError as exc:
        return _text(str(exc), status_code=400)
    platform = _platform(values, request)

    if query_name in ("GetContentByContentId", "GetLatestVersionByContentId"):
        raw_ids = query_params.get("contentids")
        if not isinstance(raw_ids, list) or not raw_ids:
            return _text("Invalid contentids", status_code=400)
        if len(raw_ids) > MAX_CONTENT_IDS:
            return _text("Too many content ids", status_code=400)
        try:
            content_ids = [_safe_content_id(value) for value in raw_ids]
        except ValueError as exc:
            return _text(str(exc), status_code=400)
        for trigger, asset in (
            (HEIST1_TRIGGER, "heists1"),
            (HEIST2_TRIGGER, "heists2"),
        ):
            if trigger in content_ids:
                try:
                    return _xml(load_asset(asset).decode("utf-8"))
                except (StaticAssetError, UnicodeDecodeError):
                    return _text("Static content is unavailable", status_code=500)

    visibility, visibility_args = _visibility_clause(user)
    clauses = [visibility]
    args: List[Any] = list(visibility_args)
    params = [field(values, "contentType")]

    with closing(get_db()) as conn:
        if query_name in ("GetMyContent", "GetMyBookmarkedContent"):
            if not user:
                return _json_error(401, "Unauthorized")
            if query_name == "GetMyContent":
                clauses.append("rockstar_id = ?")
                args.append(user["rockstar_id"])
                if params[0]:
                    normalized = _resolve_content_type(params[0])
                    if normalized:
                        clauses.append("category = ?")
                        args.append(normalized)
                if platform in LEGACY_PLATFORMS:
                    clauses.append("is_legacy = 1")
                if "published" in query_params:
                    published = query_params["published"]
                    if published is True or published == "true":
                        clauses.append("is_published = 1")
                    else:
                        clauses.append("(is_published = 0 OR is_published IS NULL)")
                where = " AND ".join(clauses)
                total = conn.execute(
                    f"SELECT COUNT(*) FROM ugc_contents WHERE {where}", args
                ).fetchone()[0]
                rows = conn.execute(
                    f"SELECT * FROM ugc_contents WHERE {where} "
                    "ORDER BY updated_date DESC LIMIT ? OFFSET ?",
                    (*args, count, offset),
                ).fetchall()
            else:
                bookmark_clause, bookmark_args = _visibility_clause(user, alias="u")
                bookmark_clauses = [bookmark_clause]
                bookmark_args = list(bookmark_args)
                if params[0]:
                    normalized = _resolve_content_type(params[0])
                    if normalized:
                        bookmark_clauses.append("u.category = ?")
                        bookmark_args.append(normalized)
                if platform in LEGACY_PLATFORMS:
                    bookmark_clauses.append("u.is_legacy = 1")
                bookmark_where = " AND ".join(bookmark_clauses)
                join = (
                    "FROM ugc_mission_bookmarks AS b "
                    "JOIN ugc_contents AS u ON u.content_id = b.content_id "
                    f"WHERE b.user_id = ? AND {bookmark_where}"
                )
                total = conn.execute(
                    f"SELECT COUNT(*) {join}", (user["id"], *bookmark_args)
                ).fetchone()[0]
                rows = conn.execute(
                    f"SELECT u.* {join} ORDER BY b.created_at DESC LIMIT ? OFFSET ?",
                    (user["id"], *bookmark_args, count, offset),
                ).fetchall()
        elif query_name in ("GetContentByContentId", "GetLatestVersionByContentId"):
            placeholders = ", ".join("?" for _ in content_ids)
            clauses.append(f"(content_id IN ({placeholders}) OR root_content_id IN ({placeholders}))")
            args.extend(content_ids)
            args.extend(content_ids)
            if platform in LEGACY_PLATFORMS:
                clauses.append("is_legacy = 1")
            where = " AND ".join(clauses)
            total = conn.execute(
                f"SELECT COUNT(*) FROM ugc_contents WHERE {where}", args
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM ugc_contents WHERE {where} "
                "ORDER BY updated_date DESC LIMIT ? OFFSET ?",
                (*args, count, offset),
            ).fetchall()
        elif query_name == "GetContentByCategory":
            category = query_params.get("category")
            if not isinstance(category, str) or not category:
                return _text("Invalid category", status_code=400)
            clauses.append("category = ?")
            args.append(category)
            if platform in LEGACY_PLATFORMS:
                clauses.append("is_legacy = 1")
            where = " AND ".join(clauses)
            total = conn.execute(
                f"SELECT COUNT(*) FROM ugc_contents WHERE {where}", args
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM ugc_contents WHERE {where} "
                "ORDER BY updated_date DESC LIMIT ? OFFSET ?",
                (*args, count, offset),
            ).fetchall()
        else:
            return _xml(_query_xml([], 0, status=0))

    return _xml(_query_xml(list(rows), int(total)))


# SOURCE_CONFIRMED: the exact LSO path is ``/gta5/11/gameservices/ugc.asmx``.
# The two bounded aliases mirror every other .asmx service in this project;
# neither is a generic catch-all.
_UGC_METHODS = (
    ("CreateContent", create_content),
    ("UpdateContent", update_content),
    ("Publish", publish_content),
    ("SetDeleted", set_deleted),
    ("QueryContent", query_content),
)
for _method, _handler in _UGC_METHODS:
    router.add_api_route(
        f"/gta5/11/gameservices/ugc.asmx/{_method}",
        _handler,
        methods=["POST"],
        tags=["lso-ugc"],
        name=f"lso_ugc_{_method.lower()}",
    )
    router.add_api_route(
        f"/{{title}}/{{version}}/{{service_set}}/ugc.asmx/{_method}",
        _handler,
        methods=["POST"],
        tags=["lso-ugc"],
        name=f"lso_ugc_{_method.lower()}_native",
    )
    router.add_api_route(
        f"/ugc.asmx/{_method}",
        _handler,
        methods=["POST"],
        tags=["lso-ugc"],
        name=f"lso_ugc_{_method.lower()}_root",
    )
