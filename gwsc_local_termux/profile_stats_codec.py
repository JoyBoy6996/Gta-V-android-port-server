"""Pure codecs for the LSO profile-stats binary formats.

``SOURCE_CONFIRMED`` from the upstream LSO source in
``src/utils/profileStats/profileStatsUtil.js``:

- stored blob / XML ``Values`` TLV: ``uint32 BE statId`` + ``int8 type`` +
  ``float32 BE``/``int32 BE``/``int64 BE`` value;
- ``ReadStatsByGamer2`` values: the same type+value pairs with the id omitted;
- ``WriteStats`` payload: one leading byte is discarded, the remainder is raw
  DEFLATE, and the inflated stream is a six-byte big-endian header of three
  signed 16-bit counts (``int64``, ``int32``, ``float``) followed by id+value
  records grouped in that order;
- wire types are ``INT64=0``, ``FLOAT=3`` and ``INT32=4``.

Every parser here is strict (no trailing bytes, no duplicate ids, bounded
sizes).  This is an ``ADAPTED`` hardening of upstream's permissive readers:
malformed client data is rejected rather than silently producing a partial
stat map.  The discarded first DEFLATE byte is deliberately treated as opaque;
no version field or checksum is invented.  No player values or opaque captures
are embedded in this module -- callers supply all bytes.
"""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass
from typing import Iterable, List, Mapping

TYPE_INT64 = 0
TYPE_FLOAT = 3
TYPE_INT32 = 4
WIRE_TYPES = frozenset((TYPE_INT64, TYPE_FLOAT, TYPE_INT32))

# SOURCE_CONFIRMED: getExpectedProfileStatType() in profileStatsUtil.js:39-83.
# DB stat types 3 (string), 0 and unknown values have no numeric wire type and
# are intentionally absent here.
_WIRE_TYPE_BY_STAT_TYPE: dict[int, int] = {}
for _stat_type in (1, 4, 5, 6, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 22, 25):
    _WIRE_TYPE_BY_STAT_TYPE[_stat_type] = TYPE_INT32
for _stat_type in (7, 8, 20, 21, 23, 24, 26):
    _WIRE_TYPE_BY_STAT_TYPE[_stat_type] = TYPE_INT64
_WIRE_TYPE_BY_STAT_TYPE[2] = TYPE_FLOAT

_UINT32_MAX = (1 << 32) - 1
_INT32_MIN, _INT32_MAX = -(1 << 31), (1 << 31) - 1
_INT64_MIN, _INT64_MAX = -(1 << 63), (1 << 63) - 1

STAT_ID_MAX_COUNT = 4096
GROUP_ID_MAX_COUNT = 512
PACKED_STAT_MAX_COUNT = 8192
DEFAULT_MAX_BLOB_BYTES = 1 << 20


class ProfileStatsCodecError(ValueError):
    """A stats payload is malformed, unsupported or exceeds a bound."""


@dataclass(frozen=True)
class StatValue:
    """One typed stat.  ``type`` is always one of :data:`WIRE_TYPES`."""

    stat_id: int
    type: int
    value: int | float

    def __post_init__(self) -> None:
        if not isinstance(self.stat_id, int) or not 0 <= self.stat_id <= _UINT32_MAX:
            raise ProfileStatsCodecError("Invalid stat id")
        if self.type not in WIRE_TYPES:
            raise ProfileStatsCodecError("Invalid stat wire type")
        if self.type == TYPE_INT64:
            if type(self.value) is not int or not _INT64_MIN <= self.value <= _INT64_MAX:
                raise ProfileStatsCodecError("int64 stat value is out of range")
        elif self.type == TYPE_INT32:
            if type(self.value) is not int or not _INT32_MIN <= self.value <= _INT32_MAX:
                raise ProfileStatsCodecError("int32 stat value is out of range")
        elif isinstance(self.value, float):
            if not math.isfinite(self.value):
                raise ProfileStatsCodecError("float stat value must be finite")
        elif not isinstance(self.value, (int, float)):
            raise ProfileStatsCodecError("Invalid float stat value")


def expected_wire_type(stat_type: object) -> int | None:
    """Map an upstream DB ``Type`` to a wire type, or ``None`` if unsupported."""
    try:
        parsed = int(str(stat_type).strip(), 0)
    except (TypeError, ValueError):
        return None
    return _WIRE_TYPE_BY_STAT_TYPE.get(parsed)


def parse_stat_ids(raw: bytes, *, maximum: int = STAT_ID_MAX_COUNT) -> List[int]:
    """Decode the base64-decoded ``statIds`` field: concatenated BE uint32s."""
    if not isinstance(raw, (bytes, bytearray)):
        raise ProfileStatsCodecError("statIds must be bytes")
    data = bytes(raw)
    if len(data) % 4:
        raise ProfileStatsCodecError("statIds length is not a multiple of four")
    count = len(data) // 4
    if count > maximum:
        raise ProfileStatsCodecError("Too many stat ids")
    if not count:
        return []
    return list(struct.unpack(f">{count}I", data))


def parse_group_ids(raw: bytes, *, maximum: int = GROUP_ID_MAX_COUNT) -> List[int]:
    """Decode the base64-decoded ``groupIds`` field: signed BE int32s."""
    if not isinstance(raw, (bytes, bytearray)):
        raise ProfileStatsCodecError("groupIds must be bytes")
    data = bytes(raw)
    if len(data) % 4:
        raise ProfileStatsCodecError("groupIds length is not a multiple of four")
    count = len(data) // 4
    if count > maximum:
        raise ProfileStatsCodecError("Too many group ids")
    if not count:
        return []
    return list(struct.unpack(f">{count}i", data))


def _value_size(stat_type: int) -> int:
    if stat_type == TYPE_FLOAT or stat_type == TYPE_INT32:
        return 4
    return 8


def _unpack_value(stat_type: int, raw: bytes) -> int | float:
    if stat_type == TYPE_FLOAT:
        return struct.unpack(">f", raw)[0]
    if stat_type == TYPE_INT32:
        return struct.unpack(">i", raw)[0]
    return struct.unpack(">q", raw)[0]


def parse_stats_blob(
    raw: bytes, *, maximum: int = DEFAULT_MAX_BLOB_BYTES
) -> dict[int, StatValue]:
    """Parse a base64-decoded stored stats blob into a stat-id map."""
    if not isinstance(raw, (bytes, bytearray)):
        raise ProfileStatsCodecError("stats blob must be bytes")
    data = bytes(raw)
    if len(data) > maximum:
        raise ProfileStatsCodecError("Stats blob is too large")

    result: dict[int, StatValue] = {}
    offset = 0
    while offset < len(data):
        if len(data) - offset < 5:
            raise ProfileStatsCodecError("Truncated stat header")
        stat_id = struct.unpack_from(">I", data, offset)[0]
        stat_type = data[offset + 4]
        if stat_type not in WIRE_TYPES:
            raise ProfileStatsCodecError("Unknown stat wire type")
        width = _value_size(stat_type)
        end = offset + 5 + width
        if end > len(data):
            raise ProfileStatsCodecError("Truncated stat value")
        value = _unpack_value(stat_type, data[offset + 5 : end])
        if stat_id in result:
            raise ProfileStatsCodecError("Duplicate stat id")
        result[stat_id] = StatValue(stat_id, stat_type, value)
        offset = end
    return result


def _pack_value(stat: StatValue) -> bytes:
    if stat.type == TYPE_FLOAT:
        return struct.pack(">f", stat.value)
    if stat.type == TYPE_INT32:
        return struct.pack(">i", stat.value)
    return struct.pack(">q", stat.value)


def _ordered(entries: Iterable[StatValue] | Mapping[int, StatValue]) -> List[StatValue]:
    values = list(entries.values()) if isinstance(entries, Mapping) else list(entries)
    for value in values:
        if not isinstance(value, StatValue):
            raise ProfileStatsCodecError("Expected a StatValue")
    return values


def write_stats_blob(entries: Iterable[StatValue] | Mapping[int, StatValue]) -> bytes:
    """Serialise stats as the source-confirmed id + type + value TLV stream."""
    chunks: list[bytes] = []
    for stat in _ordered(entries):
        chunks.append(struct.pack(">IB", stat.stat_id, stat.type))
        chunks.append(_pack_value(stat))
    return b"".join(chunks)


def write_stats_only_values(
    entries: Iterable[StatValue] | Mapping[int, StatValue],
) -> bytes:
    """Serialise type + value pairs without ids (``ReadStatsByGamer2`` shape)."""
    chunks: list[bytes] = []
    for stat in _ordered(entries):
        chunks.append(bytes((stat.type,)))
        chunks.append(_pack_value(stat))
    return b"".join(chunks)


def decompress_write_payload(
    raw: bytes,
    *,
    max_input_bytes: int,
    max_output_bytes: int = DEFAULT_MAX_BLOB_BYTES,
) -> bytes:
    """Discard the opaque first byte and inflate the remaining raw DEFLATE."""
    if not isinstance(raw, (bytes, bytearray)):
        raise ProfileStatsCodecError("Compressed stats must be bytes")
    data = bytes(raw)
    if not 2 <= len(data) <= max_input_bytes:
        raise ProfileStatsCodecError("Compressed stats are empty or too large")
    decompressor = zlib.decompressobj(wbits=-zlib.MAX_WBITS)
    try:
        inflated = decompressor.decompress(data[1:], max_output_bytes + 1)
    except zlib.error as exc:
        raise ProfileStatsCodecError("Invalid compressed stats") from exc
    if len(inflated) > max_output_bytes or decompressor.unconsumed_tail:
        raise ProfileStatsCodecError("Decompressed stats are too large")
    try:
        inflated += decompressor.flush()
    except zlib.error as exc:
        raise ProfileStatsCodecError("Invalid compressed stats") from exc
    if len(inflated) > max_output_bytes:
        raise ProfileStatsCodecError("Decompressed stats are too large")
    if not decompressor.eof or decompressor.unused_data:
        raise ProfileStatsCodecError("Truncated or trailing compressed stats")
    return inflated


def parse_packed_stats(
    data: bytes, *, maximum: int = PACKED_STAT_MAX_COUNT
) -> List[StatValue]:
    """Parse the inflated ``WriteStats`` payload with its three-count header."""
    if not isinstance(data, (bytes, bytearray)):
        raise ProfileStatsCodecError("Packed stats must be bytes")
    raw = bytes(data)
    if len(raw) < 6:
        raise ProfileStatsCodecError("Truncated packed stats header")
    int64_count, int32_count, float_count = struct.unpack_from(">hhh", raw)
    counts = (int64_count, int32_count, float_count)
    if any(count < 0 for count in counts) or sum(counts) > maximum:
        raise ProfileStatsCodecError("Invalid packed stat count")
    expected = 6 + int64_count * 12 + (int32_count + float_count) * 8
    if len(raw) != expected:
        raise ProfileStatsCodecError("Packed stats length does not match its header")

    values: list[StatValue] = []
    offset = 6
    for count, stat_type, width in (
        (int64_count, TYPE_INT64, 8),
        (int32_count, TYPE_INT32, 4),
        (float_count, TYPE_FLOAT, 4),
    ):
        for _ in range(count):
            stat_id = struct.unpack_from(">I", raw, offset)[0]
            value = _unpack_value(stat_type, raw[offset + 4 : offset + 4 + width])
            values.append(StatValue(stat_id, stat_type, value))
            offset += 4 + width
    seen: set[int] = set()
    for stat in values:
        if stat.stat_id in seen:
            raise ProfileStatsCodecError("Duplicate stat id")
        seen.add(stat.stat_id)
    return values
