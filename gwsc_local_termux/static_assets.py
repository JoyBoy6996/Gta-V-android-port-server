"""Immutable, source-confirmed static game assets.

``SOURCE_CONFIRMED``: the upstream LSO server serves pre-encrypted tunables,
RPF bgscripts, small XML/JSON compatibility files and a static Xbox 360 news
list from files under ``src/static``.  The blobs vendored here are byte-exact
copies with recorded SHA-256 digests; they are opaque bytes and are never
parsed, decrypted or logged.  No encryption keys are copied.

Every asset is verified on first use.  A missing or modified blob fails closed:
the route returns an error instead of serving unverified bytes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

STATIC_DIR = Path(__file__).resolve().parent / "static"


class StaticAssetError(RuntimeError):
    """A vendored asset is missing, truncated or modified."""


@dataclass(frozen=True)
class StaticAsset:
    relative_path: str
    size: int
    sha256: str
    media_type: str

    @property
    def path(self) -> Path:
        # The path is a hardcoded constant from this module, never client input.
        return STATIC_DIR.joinpath(*self.relative_path.split("/"))


# Digests and byte counts were computed from the upstream checkout at commit
# a22f826.  These are compatibility bytes, not credentials.
ASSETS: Dict[str, StaticAsset] = {
    "ps4_tunables": StaticAsset(
        "tunables02192026.bin",
        22151,
        "239efcc2c6af38424cd0489ae27fdc0cbf5246896d4d3726158d79a5f927b5e6",
        "application/octet-stream",
    ),
    "ps2_tunables": StaticAsset(
        "0x1a098062PS4.json",
        18484,
        "f996aaa312f9e05d6a118482fc6d3d4a153acbee05f4c091984a51708786c6d6",
        "application/octet-stream",
    ),
    "ps5_tunables": StaticAsset(
        "ps5/tunables-ps5-reencrypted.bin",
        33427,
        "6cc10b384873446a273254592857c074fde28ab46d8301dcb917dd5ac72299fd",
        "application/octet-stream",
    ),
    "ps5_bgscript": StaticAsset(
        "ps5/bg_ng_344_15.rpf",
        22528,
        "9e218fff525f71dd789e8cbf32999d3da55e12ce2ab98364eed8e80b9145e162",
        "application/octet-stream",
    ),
    "xbox360_bgscript": StaticAsset(
        "rockstar/bg_900_0.rpf",
        8192,
        "a6fcc6ce2bbdffbf466df9b44025588c9138d546f911d217025a7c17d47a655f",
        "application/octet-stream",
    ),
    "check_json": StaticAsset(
        "rockstar/check.json",
        2,
        "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
        "application/json",
    ),
    "extra_content_manifest": StaticAsset(
        "rockstar/ExtraContentManifest.xml",
        761,
        "6a435d4579fd12c570c698dfd841d646f995c00ed19f4d0f59891fdc9a7fd7b9",
        "application/xml",
    ),
    "version_num": StaticAsset(
        "ps4/version_num.xml",
        1029,
        "980e55e4877b3542166b294ad098c2b611239933fa79ea8cec8cf4a75637c49b",
        "application/xml",
    ),
    "xbox360_news": StaticAsset(
        "news.json",
        255,
        "ac2fc41fbb98f64400fd63212b66c248d24f1f74d957bc91ead4558df6b7e39c",
        "application/json",
    ),
    "ps4_commerce": StaticAsset(
        "ps4/commerceData.xml",
        7898,
        "fd3b6262f4d8e1714d86a78411e99d99e2dfc6c06f4410455216f375d4833524",
        "application/xml",
    ),
    "xbox360_commerce": StaticAsset(
        "rockstar/commerceData.xml",
        28252,
        "8c0e255a045af601d776ec7a344631305a2bdb948b7c0deb850c2337bdd67bb4",
        "application/xml",
    ),
    # SOURCE_CONFIRMED: QueryContent returns these verbatim for the two legacy
    # heist trigger IDs.  They are static catalog data, not user content.
    "heists1": StaticAsset(
        "Heists1.xml",
        12726,
        "4bbf757913b98ec14843fa320c90c152d99e4893869699f041e60bf6631c41e4",
        "application/xml",
    ),
    "heists2": StaticAsset(
        "Heists2.xml",
        17664,
        "c4d9c49eba716486bf359c745eadcaf88fd94f755b924f99f985ff3607bc461f",
        "application/xml",
    ),
}

_CACHE: Dict[str, bytes] = {}


def load_asset(name: str) -> bytes:
    """Return verified immutable bytes for a named asset."""
    asset = ASSETS.get(name)
    if asset is None:
        raise StaticAssetError("Unknown static asset")
    cached = _CACHE.get(name)
    if cached is not None:
        return cached
    try:
        raw = asset.path.read_bytes()
    except OSError as exc:
        raise StaticAssetError("Static asset is unavailable") from exc
    if len(raw) != asset.size or hashlib.sha256(raw).hexdigest() != asset.sha256:
        raise StaticAssetError("Static asset failed integrity verification")
    _CACHE[name] = raw
    return raw
