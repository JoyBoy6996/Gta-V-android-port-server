"""Account-scoped profile-stat blob storage.

``SOURCE_CONFIRMED``: upstream LSO stores one base64 text blob per account at
``<platform>/statsStorage/<rockstarId>/profileStats.txt``.  ``ADAPTED`` here:

- the account id comes only from authenticated request state, never a path or
  gamer-handle field;
- the platform is validated against the exact source-confirmed set and mapped
  to a fixed prefix, so a client cannot choose another storage namespace;
- keys go through :func:`object_storage.account_key`, which re-validates every
  component and keeps stats separate from saves, news and UGC;
- a missing blob is an empty stat map, never a generated "default player";
- a corrupt blob is an explicit error, never a silent zero-filled response.

The stored bytes are the source-confirmed base64 TLV stream; the opaque DEFLATE
wrapper applies only to ``WriteStats`` request payloads.
"""

from __future__ import annotations

import base64
import binascii
import threading
from typing import Mapping

from . import object_storage
from .profile_stats_codec import StatValue, parse_stats_blob, write_stats_blob

# SOURCE_CONFIRMED: the platform-to-prefix map duplicated in all three upstream
# ProfileStats handlers.  Values are fixed, never derived from the request Host.
PLATFORM_PREFIXES: dict[str, str] = {
    "PS3": "ps3",
    "PS4": "ps4",
    "PS5": "ps5",
    "XBOX360": "xbox360",
    "XBOXONE": "xboxone",
    "PCROS": "pc",
}

STATS_OBJECT_NAME = "profileStats.txt"


class ProfileStatsStoreError(RuntimeError):
    """Profile stats could not be read or written safely."""


class ProfileStatsCorruptError(ProfileStatsStoreError):
    """Stored profile stats are not a valid source-confirmed blob."""


_write_lock = threading.Lock()


def resolve_platform(value: object) -> str | None:
    """Return a canonical platform name, or ``None`` for an unsupported value."""
    if not isinstance(value, str):
        return None
    candidate = value.strip().upper()
    return candidate if candidate in PLATFORM_PREFIXES else None


def stats_key(user: Mapping[str, object], platform: str) -> tuple[str, ...]:
    prefix = PLATFORM_PREFIXES.get(platform)
    if prefix is None:
        raise ProfileStatsStoreError("Unsupported platform for profile stats")
    return object_storage.account_key(
        user["id"], "stats", prefix, STATS_OBJECT_NAME
    )


def read_stats(user: Mapping[str, object], platform: str) -> dict[int, StatValue]:
    """Return only stats actually present in the authenticated account's blob."""
    store = object_storage.get_object_store(profile="default")
    try:
        stored = store.get(stats_key(user, platform))
    except object_storage.ObjectNotFoundError:
        return {}
    except object_storage.ObjectStorageError as exc:
        raise ProfileStatsStoreError("Could not read profile stats") from exc

    try:
        raw = base64.b64decode(stored.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ProfileStatsCorruptError("Stored profile stats are not valid base64") from exc
    try:
        return parse_stats_blob(raw)
    except ValueError as exc:
        raise ProfileStatsCorruptError("Stored profile stats are malformed") from exc


def write_stats(
    user: Mapping[str, object], platform: str, stats: Mapping[int, StatValue]
) -> None:
    """Replace the account's blob with ``stats`` (caller owns the merge)."""
    encoded = base64.b64encode(write_stats_blob(stats))
    store = object_storage.get_object_store(profile="default")
    try:
        store.put(
            stats_key(user, platform), encoded, content_type="text/plain"
        )
    except object_storage.ObjectStorageError as exc:
        raise ProfileStatsStoreError("Could not write profile stats") from exc


def merge_stats(
    user: Mapping[str, object],
    platform: str,
    updates: Mapping[int, StatValue],
) -> dict[int, StatValue]:
    """Atomically merge accepted updates into the account's real stored stats."""
    with _write_lock:
        current = read_stats(user, platform)
        merged = dict(current)
        merged.update(updates)
        write_stats(user, platform, merged)
        return merged
