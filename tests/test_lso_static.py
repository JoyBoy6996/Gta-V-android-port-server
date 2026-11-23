"""Static/cloud asset parity tranche: byte-exact assets and safe redirects."""

import hashlib
import importlib.util
import io
from contextlib import closing

import pytest

from gwsc_local_termux import config, database, object_storage
from gwsc_local_termux.routes.lso_static import GLOBAL_CACHE
from gwsc_local_termux.static_assets import ASSETS, load_asset
from gwsc_local_termux.time_utils import now_iso

CLOUD = "/cloud/11/cloudservices"


def _jpeg_bytes() -> bytes:
    if importlib.util.find_spec("PIL"):
        from PIL import Image

        output = io.BytesIO()
        Image.new("RGB", (4, 4), (20, 40, 60)).save(output, format="JPEG")
        return output.getvalue()
    # Pillow is optional; without it the route validates magic bytes only.
    return b"\xff\xd8\xff" + b"opaque-jpeg-source"


def _seed_clan(clan_id, account, *, emblem_path="", is_open=0):
    with closing(database.get_db()) as conn:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO clans "
                "(id, name, tag, emblem_path, is_open_clan, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (clan_id, "Test Crew", "TST", emblem_path, is_open, now_iso()),
            )
            conn.execute(
                "INSERT OR REPLACE INTO clan_memberships "
                "(user_id, clan_id, joined_at) VALUES (?, ?, ?)",
                (
                    conn.execute(
                        "SELECT id FROM users WHERE rockstar_id = ?",
                        (account["profile"]["rockstarId"],),
                    ).fetchone()["id"],
                    clan_id,
                    now_iso(),
                ),
            )


def test_vendored_assets_match_recorded_digests():
    for name, asset in ASSETS.items():
        raw = load_asset(name)
        assert len(raw) == asset.size, name
        assert hashlib.sha256(raw).hexdigest() == asset.sha256, name


@pytest.mark.parametrize(
    "path,asset_name",
    [
        (f"{CLOUD}/titles/gta5/ps4/0x1a098062.json", "ps4_tunables"),
        ("/titles/gta5/ps2/0x1a098062.json", "ps2_tunables"),
        (f"{CLOUD}/titles/gta5/ps5/0x1a098062.json", "ps5_tunables"),
        (f"{CLOUD}/titles/gta5/xbox360/news/news.json", "xbox360_news"),
        ("/titles/gta5/xbox360/check.json", "check_json"),
        (
            "/titles/gta5/xbox360/extraContent/ExtraContentManifest.xml",
            "extra_content_manifest",
        ),
        (
            f"{CLOUD}/titles/gta5/ps5/legal/version/version_num.xml",
            "version_num",
        ),
        (
            f"{CLOUD}/titles/gta5/ps4/legal/version/version_num.xml",
            "version_num",
        ),
        ("/titles/gta5/xbox360/bgscripts/bg_900_0.rpf", "xbox360_bgscript"),
        (
            f"{CLOUD}/titles/gta5/ps5/bgscripts/bg_ng_171_8.rpf",
            "ps5_bgscript",
        ),
        ("/titles/gta5/ps4/store/SCEA/en/commerceData.xml", "ps4_commerce"),
        ("/titles/gta5/xbox360/store/en/commerceData.xml", "xbox360_commerce"),
    ],
)
def test_asset_routes_serve_verified_bytes(client, path, asset_name):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 200, response.text
    assert response.content == load_asset(asset_name)
    assert response.headers["content-type"] == ASSETS[asset_name].media_type
    assert response.headers["cache-control"] == GLOBAL_CACHE
    assert "strict-transport-security" not in response.headers


def test_asset_route_supports_head_and_validates_supplied_ticket(client):
    path = "/titles/gta5/ps2/0x1a098062.json"
    assert client.head(path).status_code == 200

    response = client.get(path, headers={"ros-SessionTicket": "not-a-ticket"})
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_dynamic_ps3_tunables_are_deliberately_unsupported(client):
    response = client.get("/titles/gta5/ps3/0x1a098062.json")
    assert response.status_code == 501
    assert response.json()["error"] == "unsupported"
    assert "crypto contract" in response.json()["message"]


def test_xboxone_tunables_redirect_to_local_asset(client):
    response = client.get(
        f"{CLOUD}/titles/gta5/xboxone/0x1a098062.json", follow_redirects=False
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/titles/gta5/ps2/0x1a098062.json"
    assert response.headers["cache-control"] == GLOBAL_CACHE


def _allow_redirects(monkeypatch):
    monkeypatch.setattr(
        config, "STATIC_REDIRECT_PROD_CLOUD", "https://prod.example.invalid"
    )
    monkeypatch.setattr(
        config, "STATIC_REDIRECT_TUNABLES", "https://tunables.example.invalid"
    )
    monkeypatch.setattr(config, "STATIC_REDIRECT_ROS", "https://ros.example.invalid")
    monkeypatch.setattr(
        config,
        "STATIC_REDIRECT_ALLOWED_HOSTS",
        frozenset({"prod.example.invalid", "tunables.example.invalid", "ros.example.invalid"}),
    )


@pytest.mark.parametrize(
    "path,target",
    [
        (
            f"{CLOUD}/titles/gta5/pcros/0x1a098062.json",
            "https://prod.example.invalid/titles/gta5/pcros/0x1a098062.json",
        ),
        (
            f"{CLOUD}/titles/gta5/xbox360/0x1a098062.json",
            "https://tunables.example.invalid/titles/gta5/ps3/0x1a098062.json",
        ),
        (
            f"{CLOUD}/titles/gta5/ps4/check.json",
            "https://prod.example.invalid/titles/gta5/ps4/check.json",
        ),
        (
            f"{CLOUD}/titles/gta5/xboxone/check.json",
            "https://prod.example.invalid/titles/gta5/xboxone/check.json",
        ),
        (
            f"{CLOUD}/titles/gta5/xbox360/check.json",
            "https://tunables.example.invalid/titles/gta5/xbox360/check.json",
        ),
        (
            f"{CLOUD}/titles/gta5/xboxone/extraContent/ExtraContentManifest.xml",
            "https://prod.example.invalid/titles/gta5/xboxone/extraContent/ExtraContentManifest.xml",
        ),
        (
            f"{CLOUD}/titles/gta5/ps4/extraContent/ExtraContentManifest.xml",
            "https://prod.example.invalid/titles/gta5/ps4/extraContent/ExtraContentManifest.xml",
        ),
        (
            f"{CLOUD}/titles/gta5/xbox360/extraContent/ExtraContentManifest.xml",
            "https://tunables.example.invalid/titles/gta5/xbox360/extraContent/ExtraContentManifest.xml",
        ),
        (
            f"{CLOUD}/titles/gta5/ps4/bgscripts/bg_902_0.rpf",
            "https://prod.example.invalid/cloud/11/cloudservices/titles/gta5/xboxone/bgscripts/bg_ng_2843_0.rpf",
        ),
        (
            f"{CLOUD}/titles/gta5/xbox360/bgscripts/bg_900_0.rpf",
            "https://tunables.example.invalid/titles/gta5/xbox360/bgscripts/bg_900_0.rpf",
        ),
        (
            f"{CLOUD}/titles/gta5/xbox360/store/en/commerceData.xml",
            "https://tunables.example.invalid/titles/gta5/xbox360/store/en/commerceData.xml",
        ),
        (
            f"{CLOUD}/titles/gta5/ps4/store/commerceData.xml",
            "https://ros.example.invalid/titles/gta5/ps4/store/SCEA/en/commerceData.xml",
        ),
    ],
)
def test_configured_redirects_are_exact_302(client, monkeypatch, path, target):
    _allow_redirects(monkeypatch)
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == target
    assert response.headers["cache-control"] == GLOBAL_CACHE


def test_redirect_targets_fail_closed(client, monkeypatch):
    path = f"{CLOUD}/titles/gta5/ps4/check.json"
    monkeypatch.setattr(config, "STATIC_REDIRECT_PROD_CLOUD", "")
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 404
    assert response.json()["error"] == "redirect_not_configured"

    _allow_redirects(monkeypatch)
    monkeypatch.setattr(config, "STATIC_REDIRECT_PROD_CLOUD", "http://prod.example.invalid")
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 500
    assert response.json()["error"] == "redirect_misconfigured"

    monkeypatch.setattr(
        config, "STATIC_REDIRECT_PROD_CLOUD", "https://evil.example.invalid"
    )
    assert client.get(path, follow_redirects=False).status_code == 500

    monkeypatch.setattr(
        config, "STATIC_REDIRECT_PROD_CLOUD", "https://user:pass@prod.example.invalid"
    )
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 500
    assert "pass" not in response.text
    assert "user" not in response.text


def test_image_redirect_validates_name(client, monkeypatch):
    _allow_redirects(monkeypatch)
    path = f"{CLOUD}/titles/gta5/ps4/store/images/editions/editions.jpg"
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].endswith(
        "/titles/gta5/ps4/store/images/editions/editions.jpg"
    )

    response = client.get(f"{CLOUD}/titles/gta5/xbox360/store/images/editions/bad!name")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_placeholder_json_routes_keep_source_confirmed_shape(client):
    dog = client.get(f"{CLOUD}/members/sc/1/GTA5/dog/appnpps4.json")
    assert dog.status_code == 200
    assert dog.json()["ownerID"] == "Jorby"
    assert dog.headers["content-type"].startswith("application/json")

    car = client.get(f"{CLOUD}/members/sc/1/GTA5/car/appnpps4.json")
    assert car.status_code == 200
    assert "companion_waypoints" in car.json()["blockName"]


def test_crew_emblem_requires_auth_and_membership(client, make_account):
    account = make_account(email="emblem@example.invalid", nickname="emblemuser")
    other = make_account(email="emblem-other@example.invalid", nickname="emblemother")
    source = _jpeg_bytes()
    object_storage.get_object_store().put(
        ("emblems", "71", "main.jpg"), source, content_type="image/jpeg"
    )
    _seed_clan(71, account, emblem_path="/emblems/71/main.jpg")
    path = f"{CLOUD}/crews/sc/71/publish/emblem/emblem_64.dds"

    assert client.get(path).status_code == 401
    response = client.get(path, headers=other["headers"])
    assert response.status_code == 403
    assert response.text == "Forbidden"

    response = client.get(path, headers=account["headers"])
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.headers["cache-control"] == GLOBAL_CACHE
    if importlib.util.find_spec("PIL"):
        from PIL import Image

        with Image.open(io.BytesIO(response.content)) as image:
            assert image.size == (64, 64)
    else:
        assert response.content == source


@pytest.mark.parametrize(
    "path,status,message",
    [
        (f"{CLOUD}/crews/sc/nope/publish/emblem/emblem_64.dds", 400, "Invalid clan id"),
        (f"{CLOUD}/crews/sc/72/publish/emblem/emblem_0.dds", 400, "Invalid emblem size"),
        (f"{CLOUD}/crews/sc/72/publish/emblem/emblem_1025.dds", 400, "Invalid emblem size"),
        (f"{CLOUD}/crews/sc/72/publish/emblem/emblem_64.dds", 404, "Emblem not found"),
    ],
)
def test_crew_emblem_validates_inputs(client, make_account, path, status, message):
    account = make_account(email="emblem-bad@example.invalid", nickname="emblembad")
    response = client.get(path, headers=account["headers"])
    assert response.status_code == status
    assert response.text == message


def test_crew_emblem_rejects_unsafe_keys_and_formats(client, make_account):
    account = make_account(email="emblem-key@example.invalid", nickname="emblemkey")
    store = object_storage.get_object_store()
    store.put(("saves", "bad.jpg"), b"\xff\xd8\xff", content_type="image/jpeg")
    store.put(("emblems", "73", "bad.jpg"), b"not-an-image", content_type="image/jpeg")

    _seed_clan(73, account, emblem_path="../saves/bad.jpg")
    response = client.get(
        f"{CLOUD}/crews/sc/73/publish/emblem/emblem_32.dds",
        headers=account["headers"],
    )
    assert response.status_code == 404

    with closing(database.get_db()) as conn:
        with conn:
            conn.execute(
                "UPDATE clans SET emblem_path = ? WHERE id = ?",
                ("emblems/73/bad.jpg", 73),
            )
    response = client.get(
        f"{CLOUD}/crews/sc/73/publish/emblem/emblem_32.dds",
        headers=account["headers"],
    )
    assert response.status_code == 415


def test_crew_emblem_allows_open_clan_members(client, make_account):
    account = make_account(email="emblem-open@example.invalid", nickname="emblemopen")
    other = make_account(email="emblem-open2@example.invalid", nickname="emblemopen2")
    object_storage.get_object_store().put(
        ("emblems", "74", "main.jpg"), _jpeg_bytes(), content_type="image/jpeg"
    )
    # Only the other account is a member; the clan is explicitly open.
    _seed_clan(74, other, emblem_path="emblems/74/main.jpg", is_open=1)
    response = client.get(
        f"{CLOUD}/crews/sc/74/publish/emblem/emblem_16.dds",
        headers=account["headers"],
    )
    assert response.status_code == 200
