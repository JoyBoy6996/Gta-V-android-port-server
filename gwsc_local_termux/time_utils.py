"""Small UTC time helpers used across the backend.

All persisted timestamps are ISO-8601 strings in UTC.  ISO strings produced
by :func:`now_iso` sort lexicographically in the same order as the instants
they represent, which lets SQLite compare them without custom functions.
"""

import datetime
from typing import Optional

UTC = datetime.timezone.utc


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(UTC)


def now_iso() -> str:
    return utcnow().isoformat()


def iso_after(seconds: int) -> str:
    return (utcnow() + datetime.timedelta(seconds=seconds)).isoformat()


def parse_iso(value: Optional[str]) -> Optional[datetime.datetime]:
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def is_expired(value: Optional[str]) -> bool:
    parsed = parse_iso(value)
    return parsed is None or parsed <= utcnow()


def seconds_until(value: Optional[str]) -> int:
    parsed = parse_iso(value)
    if parsed is None:
        return 0
    return max(0, int((parsed - utcnow()).total_seconds()))
