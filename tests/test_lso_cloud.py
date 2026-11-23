"""Cloud persistence parity tranche: saves, news, UGC binaries and uploads."""

import json
import time
import urllib.parse
from contextlib import closing
from xml.etree import ElementTree as ET

import pytest

from gwsc_local_termux import config, database, object_storage
from gwsc_local_termux.routes.lso_cloud import GLOBAL_CACHE, issue_photo_upload_token
from gwsc_local_termux.time_utils import now_iso

CLOUD = "/cloud/11/cloudservices"


def _text(root, name):
    return root.findtext(f".//{{*}}{name}")


def _account_id(account):
    with closing(database.get_db()) as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE rockstar_id = ?",
            (account["profile"]["rockstarId"],),
        ).fetchone()
    assert row is not None
    return row["id"]


def _xml_root(response):
    return ET.fromstring(response.text)


def _token_query(token):
    return "?" + urllib.parse.urlencode({"token": token})


def _seed_news(story_key="STORY1", *, date=None, expire=0, active=1):
    current = int(time.time())
    with closing(database.get_db()) as conn:
        with conn:
            return conn.execute(
                """
                INSERT INTO news
                    (story_key, types, date, expire, title, headline, subtitle,
                     content, url, style, priority, secondary_priority,
                     tracking_id, image_path, image_filesize, image_type,
                     extra_data, language, active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    story_key,
                    json.dumps(["news", "feature"]),
                    current if date is None else date,
                    expire,
                    "Test Story",
                    "Test Headline",
                    "Test Subtitle",
                    "Story body",
                    "https://example.invalid/story",
                    "default",
                    7,
                    3,
                    "track-1",
                    "42/pic.jpg",
                    1234,
                    "portrait",
                    json.dumps({"badge": "new"}),
                    "en",
                    active,
                    now_iso(),
                ),
            ).lastrowid


def _seed_ugc_content(content_id, account, *, category="gta5photo", published=0):
    with closing(database.get_db()) as conn:
        with conn:
            conn.execute(
                """
                INSERT INTO ugc_contents
                    (content_id, category, created_date, account_id, rockstar_id,
                     user_id, is_published)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    content_id,
                    category,
                    now_iso(),
                    _account_id(account),
                    account["profile"]["rockstarId"],
                    _account_id(account),
                    published,
                ),
            )


# ---------------------------------------------------------------------------
# Save files
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path_platform,store_platform",
    [("sc", "PCROS"), ("np", "PS4"), ("xbl", "XBOXONE")],
)
def test_save_round_trip_is_account_scoped(client, make_account, path_platform, store_platform):
    account = make_account(
        email=f"save-{path_platform}@example.invalid", nickname=f"save{path_platform}"
    )
    payload = b"\x00save-data\xff"
    url = f"{CLOUD}/members/{path_platform}/99999/GTA5/saves/mpstats"

    response = client.post(
        url,
        headers=account["headers"],
        files={"file": ("slot1", payload, "application/octet-stream")},
    )
    assert response.status_code == 200, response.text
    root = _xml_root(response)
    assert root.get("ms") == "0"
    assert _text(root, "Status") == "1"

    expected_key = object_storage.account_key(
        _account_id(account), "saves", store_platform, "slot1"
    )
    assert object_storage.get_object_store().get(expected_key) == payload

    response = client.get(f"{url}/slot1", headers=account["headers"])
    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["cache-control"] == "private, max-age=0"


def test_save_path_id_cannot_target_another_account(client, make_account):
    owner = make_account(email="save-owner@example.invalid", nickname="saveowner")
    other = make_account(email="save-other@example.invalid", nickname="saveother")
    payload = b"owner-save"

    client.post(
        f"{CLOUD}/members/sc/{owner['profile']['rockstarId']}/GTA5/saves/mpstats",
        headers=owner["headers"],
        files={"file": ("slot2", payload, "application/octet-stream")},
    )
    response = client.get(
        f"{CLOUD}/members/sc/{owner['profile']['rockstarId']}/GTA5/saves/mpstats/slot2",
        headers=other["headers"],
    )
    assert response.status_code == 404
    assert _xml_root(response).find(".//{*}Error").get("Code") == "DoesNotExist"


def test_save_requires_credentials_and_rejects_bad_inputs(client, make_account):
    url = f"{CLOUD}/members/sc/123/GTA5/saves/mpstats"
    response = client.post(
        url, files={"file": ("slot1", b"x", "application/octet-stream")}
    )
    assert response.status_code == 401
    assert _text(_xml_root(response), "Status") == "0"

    account = make_account(email="save-bad@example.invalid", nickname="savebad")
    response = client.post(
        url,
        headers=account["headers"],
        files={"file": ("../evil", b"x", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert _xml_root(response).find(".//{*}Error").get("Code") == "InvalidRequest"

    response = client.get(f"{url}/missing", headers=account["headers"])
    assert response.status_code == 404
    assert _xml_root(response).find(".//{*}Error").get("Code") == "DoesNotExist"


def test_profile_share_upload_is_account_scoped(client, make_account):
    account = make_account(email="share@example.invalid", nickname="shareuser")
    payload = b"\xff\xd8\xff-profile"
    response = client.post(
        f"{CLOUD}/members/np/{account['profile']['rockstarId']}/share/gta5/mpchars",
        headers=account["headers"],
        files={"file": ("avatar.jpg", payload, "image/jpeg")},
    )
    assert response.status_code == 200, response.text
    assert _text(_xml_root(response), "Status") == "1"
    key = object_storage.account_key(
        _account_id(account), "profile-share", "PS4", "avatar.jpg"
    )
    assert object_storage.get_object_store().get(key) == payload


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------
def test_news_list_uses_real_rows_and_filters_expired(client, make_account):
    account = make_account(email="news@example.invalid", nickname="newsuser")
    _seed_news("ACTIVE")
    _seed_news("FUTURE", date=int(time.time()) + 3600)
    _seed_news("EXPIRED", expire=1)
    _seed_news("OFF", active=0)

    response = client.get(
        f"{CLOUD}/titles/gta5/ps4/news/news.json", headers=account["headers"]
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    items = response.json()
    assert len(items) == 1
    event = items[0]["gm.evt"]
    assert event["e"] == "news"
    assert event["d"]["k"] == "ACTIVE"
    assert event["d"]["t"] == ["news", "feature"]
    assert event["d"]["p"] == 7
    assert event["d"]["sp"] == 3
    assert event["d"]["sk"] == "track-1"


def test_news_story_shape_and_availability(client, make_account):
    account = make_account(email="story@example.invalid", nickname="storyuser")
    _seed_news("STORY1")

    response = client.get(
        f"{CLOUD}/global/sc/news/STORY1/en.json", headers=account["headers"]
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["title"] == "Test Story"
    assert payload["content"] == "Story body"
    assert payload["secondaryPriority"] == 3
    assert payload["trackingId"] == "track-1"
    assert payload["headline"] == "Test Headline"
    assert payload["subtitle"] == "Test Subtitle"
    assert payload["url"].endswith("/story")
    assert payload["image"] == {
        "path": "42/pic.jpg",
        "filesize": 1234,
        "type": "portrait",
    }
    assert payload["extraData"] == {"badge": "new"}

    response = client.get(
        f"{CLOUD}/global/sc/news/MISSING/en.json", headers=account["headers"]
    )
    assert response.status_code == 404
    assert response.json() == {"error": "News story not found"}

    _seed_news("LATER", date=int(time.time()) + 3600)
    response = client.get(
        f"{CLOUD}/global/sc/news/LATER/en.json", headers=account["headers"]
    )
    assert response.status_code == 404
    assert response.json() == {"error": "News story not available"}


def test_news_image_bytes_and_errors(client, make_account):
    account = make_account(email="image@example.invalid", nickname="imageuser")
    payload = b"\x89PNG\r\n\x1a\nnews-image"
    object_storage.get_object_store(profile="news").put(
        ("news", "42", "pic.png"), payload, content_type="image/png"
    )

    response = client.get(
        f"{CLOUD}/global/news/image/42/pic.png", headers=account["headers"]
    )
    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == GLOBAL_CACHE

    response = client.get(
        f"{CLOUD}/global/news/image/42/missing.png", headers=account["headers"]
    )
    assert response.status_code == 404
    assert response.text == "Image not found"


@pytest.mark.parametrize(
    "path",
    [
        f"{CLOUD}/titles/gta5/ps4/news/news.json",
        f"{CLOUD}/global/sc/news/STORY1/en.json",
        f"{CLOUD}/global/news/image/42/pic.jpg",
    ],
)
def test_news_requires_credentials(client, path):
    assert client.get(path).status_code == 401


# ---------------------------------------------------------------------------
# UGC binary download
# ---------------------------------------------------------------------------
def test_ugc_binary_primary_and_image_fallback(client, make_account):
    account = make_account(email="ugc@example.invalid", nickname="ugcuser")
    store = object_storage.get_object_store(profile="ugc")
    store.put(("ugc", "HASH1_01.ugc"), b"mission-binary")
    store.put(("ugc", "HASH2_02.ugc"), b"fallback-photo")

    response = client.get(
        f"{CLOUD}/ugc/gta5mission/HASH1/1_mission.bin", headers=account["headers"]
    )
    assert response.status_code == 200
    assert response.content == b"mission-binary"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["cache-control"] == "private, max-age=0"

    # A 1_preview.jpg request looks for _01 first, then falls back to _02.
    response = client.get(
        f"{CLOUD}/ugc/gta5photo/HASH2/1_preview.jpg", headers=account["headers"]
    )
    assert response.status_code == 200
    assert response.content == b"fallback-photo"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "public, max-age=3600"


def test_ugc_image_default_photo(client, make_account, monkeypatch):
    account = make_account(email="ugc-default@example.invalid", nickname="ugcdefault")
    monkeypatch.setattr(config, "UGC_DEFAULT_PHOTO_KEY", "default_01.ugc")
    object_storage.get_object_store(profile="ugc").put(
        ("default_01.ugc",), b"default-photo"
    )
    response = client.get(
        f"{CLOUD}/ugc/gta5photo/HASH3/9_preview.jpg", headers=account["headers"]
    )
    assert response.status_code == 200
    assert response.content == b"default-photo"


def test_ugc_unpublished_content_is_owner_scoped(client, make_account):
    owner = make_account(email="ugc-owner@example.invalid", nickname="ugcowner")
    other = make_account(email="ugc-other@example.invalid", nickname="ugcother")
    _seed_ugc_content("PRIVATEHASH", owner)
    object_storage.get_object_store(profile="ugc").put(
        ("ugc", "PRIVATEHASH_01.ugc"), b"private-mission"
    )
    url = f"{CLOUD}/ugc/gta5mission/PRIVATEHASH/1_mission.bin"

    assert client.get(url, headers=other["headers"]).status_code == 404
    response = client.get(url, headers=owner["headers"])
    assert response.status_code == 200
    assert response.content == b"private-mission"


def test_ugc_download_errors(client, make_account):
    account = make_account(email="ugc-errors@example.invalid", nickname="ugcerrors")
    url = f"{CLOUD}/ugc/gta5mission/HASH4/1_mission.bin"
    assert client.get(url).status_code == 401

    response = client.get(
        f"{CLOUD}/ugc/gta5mission/bad!hash/1_mission.bin",
        headers=account["headers"],
    )
    assert response.status_code == 400
    assert _xml_root(response).find(".//{*}Error").get("Code") == "InvalidRequest"

    response = client.get(url, headers=account["headers"])
    assert response.status_code == 404
    assert _xml_root(response).find(".//{*}Error").get("Code") == "DoesNotExist"


# ---------------------------------------------------------------------------
# Photo upload HMAC
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("style", ["short", "cloud"])
def test_photo_upload_writes_both_keys_and_updates_db(
    client, make_account, monkeypatch, style
):
    monkeypatch.setattr(config, "PHOTO_UPLOAD_SECRET", "unit-test-upload-secret")
    account = make_account(email=f"photo-{style}@example.invalid", nickname=f"photo{style}")
    content_id = f"CONTENT{style.upper()}"
    _seed_ugc_content(content_id, account)
    payload = b"\xff\xd8\xff\xd9" + b"photo-bytes"
    token = issue_photo_upload_token(content_id, "0_0.jpg", style=style)
    if style == "short":
        url = f"/ugc/gta5photo/{content_id}/0_0.jpg"
    else:
        url = f"{CLOUD}/ugc/gta5photo/upload/{content_id}/0_0.jpg"

    response = client.post(
        url + _token_query(token),
        headers={**account["headers"], "Content-Type": "image/jpeg"},
        content=payload,
    )
    assert response.status_code == 200, response.text
    assert response.content == b""
    assert response.headers["X-Akamai-RSGShardPath"] == (
        f"/ugc/gta5photo/{content_id}/0_0.jpg"
    )
    assert response.headers["Cache-Control"] == "no-store"

    store = object_storage.get_object_store(profile="ugc")
    assert store.get(("ugc", f"{content_id}_00.ugc")) == payload
    assert store.get(("ugc", f"{content_id}_01.ugc")) == payload
    with closing(database.get_db()) as conn:
        row = conn.execute(
            "SELECT file_version0, file_version1, updated_date FROM ugc_contents "
            "WHERE content_id = ?",
            (content_id,),
        ).fetchone()
    assert row["file_version0"] == "0"
    assert row["file_version1"] == "0"
    assert row["updated_date"]


@pytest.mark.parametrize(
    "mutation,status,message",
    [
        ("no-token", 401, "Invalid upload token"),
        ("tampered-hmac", 401, "Invalid upload token"),
        ("wrong-file", 401, "Invalid upload token"),
        ("expired", 401, "Invalid upload token"),
        ("wrong-style", 401, "Invalid upload token"),
        ("empty-body", 400, "Empty upload body"),
    ],
)
def test_photo_upload_rejects_invalid_tokens_and_bodies(
    client, make_account, monkeypatch, mutation, status, message
):
    monkeypatch.setattr(config, "PHOTO_UPLOAD_SECRET", "unit-test-upload-secret")
    account = make_account(
        email=f"photobad-{mutation}@example.invalid", nickname=f"bad{mutation}"
    )
    content_id = "PHOTOBAD"
    _seed_ugc_content(content_id, account)
    now = int(time.time())
    token = issue_photo_upload_token(content_id, "0_0.jpg", style="short", now=now)
    if mutation == "tampered-hmac":
        token = token[:-1] + ("0" if token[-1] != "0" else "1")
    elif mutation == "wrong-file":
        token = issue_photo_upload_token(content_id, "1_0.jpg", style="short", now=now)
    elif mutation == "expired":
        token = issue_photo_upload_token(
            content_id, "0_0.jpg", style="short", now=now - 1000
        )
    elif mutation == "wrong-style":
        token = issue_photo_upload_token(content_id, "0_0.jpg", style="cloud", now=now)
    query = "" if mutation == "no-token" else _token_query(token)
    payload = b"" if mutation == "empty-body" else b"\xff\xd8\xff"

    response = client.post(
        f"/ugc/gta5photo/{content_id}/0_0.jpg{query}",
        headers={**account["headers"], "Content-Type": "image/jpeg"},
        content=payload,
    )
    assert response.status_code == status
    assert response.text == message


def test_photo_upload_requires_secret_and_ownership(client, make_account, monkeypatch):
    account = make_account(email="photo-owner@example.invalid", nickname="photoowner")
    other = make_account(email="photo-intruder@example.invalid", nickname="intruder")
    content_id = "PHOTOWNED"
    _seed_ugc_content(content_id, account)

    monkeypatch.setattr(config, "PHOTO_UPLOAD_SECRET", "")
    from gwsc_local_termux.object_storage import ObjectStorageConfigurationError

    with pytest.raises(ObjectStorageConfigurationError):
        issue_photo_upload_token(content_id, "0_0.jpg")
    response = client.post(
        f"/ugc/gta5photo/{content_id}/0_0.jpg",
        headers=account["headers"],
        content=b"x",
    )
    assert response.status_code == 401

    monkeypatch.setattr(config, "PHOTO_UPLOAD_SECRET", "unit-test-upload-secret")
    token = issue_photo_upload_token(content_id, "0_0.jpg")
    response = client.post(
        f"/ugc/gta5photo/{content_id}/0_0.jpg" + _token_query(token),
        headers=other["headers"],
        content=b"x",
    )
    assert response.status_code == 403
    assert response.text == "Forbidden"


def test_photo_upload_content_checks(client, make_account, monkeypatch):
    monkeypatch.setattr(config, "PHOTO_UPLOAD_SECRET", "unit-test-upload-secret")
    account = make_account(email="photo-checks@example.invalid", nickname="photochecks")
    _seed_ugc_content("WRONGCAT", account, category="gta5mission")

    token = issue_photo_upload_token("MISSING", "0_0.jpg")
    response = client.post(
        "/ugc/gta5photo/MISSING/0_0.jpg" + _token_query(token),
        headers=account["headers"],
        content=b"x",
    )
    assert response.status_code == 404
    assert response.text == "Content not found"

    token = issue_photo_upload_token("WRONGCAT", "0_0.jpg")
    response = client.post(
        "/ugc/gta5photo/WRONGCAT/0_0.jpg" + _token_query(token),
        headers=account["headers"],
        content=b"x",
    )
    assert response.status_code == 400
    assert response.text == "Invalid content type"


def test_photo_upload_requires_credentials(client, make_account, monkeypatch):
    monkeypatch.setattr(config, "PHOTO_UPLOAD_SECRET", "unit-test-upload-secret")
    account = make_account(email="photo-auth@example.invalid", nickname="photoauth")
    _seed_ugc_content("PHOTOAUTH", account)
    token = issue_photo_upload_token("PHOTOAUTH", "0_0.jpg")
    response = client.post(
        "/ugc/gta5photo/PHOTOAUTH/0_0.jpg" + _token_query(token), content=b"x"
    )
    assert response.status_code == 401
    assert response.text == "Unauthorized"


# ---------------------------------------------------------------------------
# Request limits
# ---------------------------------------------------------------------------
def test_cloud_uploads_use_object_limit_not_rest_limit(client, make_account, monkeypatch):
    account = make_account(email="limits@example.invalid", nickname="limits")
    monkeypatch.setattr(config, "MAX_BODY_BYTES", 4)
    monkeypatch.setattr(config, "OBJECT_MAX_BYTES", 4096)
    url = f"{CLOUD}/members/sc/123/GTA5/saves/mpstats"

    response = client.post(
        url,
        headers=account["headers"],
        files={"file": ("slot1", b"12345678", "application/octet-stream")},
    )
    assert response.status_code == 200, response.text

    response = client.post(
        "/gwsc/api/register",
        json={"email": "oversize@example.invalid", "nickname": "oversize", "password": "long-password"},
    )
    assert response.status_code == 413
