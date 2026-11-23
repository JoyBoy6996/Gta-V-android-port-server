"""Synthetic-vector tests for the profile-stats binary codecs.

All bytes here are generated in the test with ``struct``/``zlib``; no player
capture, opaque upstream blob or real stat value is reproduced.  These tests
verify the ``SOURCE_CONFIRMED`` layouts, not client acceptance.
"""

import struct
import zlib

import pytest

from gwsc_local_termux.profile_stats_codec import (
    TYPE_FLOAT,
    TYPE_INT32,
    TYPE_INT64,
    ProfileStatsCodecError,
    StatValue,
    decompress_write_payload,
    expected_wire_type,
    parse_group_ids,
    parse_packed_stats,
    parse_stat_ids,
    parse_stats_blob,
    write_stats_blob,
    write_stats_only_values,
)


def test_expected_wire_type_mapping():
    assert expected_wire_type(1) == TYPE_INT32
    assert expected_wire_type(26) == TYPE_INT64
    assert expected_wire_type("2") == TYPE_FLOAT
    # SOURCE_CONFIRMED: STRING and unknown DB types have no numeric wire type.
    assert expected_wire_type(3) is None
    assert expected_wire_type(0) is None
    assert expected_wire_type("not-a-number") is None


def test_parse_stat_ids_big_endian_and_bounds():
    raw = struct.pack(">III", 1, 0x80000000, 0xFFFFFFFF)
    assert parse_stat_ids(raw) == [1, 0x80000000, 0xFFFFFFFF]
    assert parse_stat_ids(b"") == []
    for bad in (b"\x00\x01\x02", b"\x00" * (4 * 4097)):
        with pytest.raises(ProfileStatsCodecError):
            parse_stat_ids(bad)


def test_parse_group_ids_are_signed_big_endian():
    raw = struct.pack(">iii", 1, -1, -2147483648)
    assert parse_group_ids(raw) == [1, -1, -2147483648]
    with pytest.raises(ProfileStatsCodecError):
        parse_group_ids(b"\x00\x01\x00")


def test_stats_blob_round_trip_and_exact_layout():
    stats = [
        StatValue(1, TYPE_INT64, -9007199254740993),
        StatValue(0xFFFFFFFF, TYPE_INT32, -7),
        StatValue(42, TYPE_FLOAT, 1.5),
    ]
    encoded = write_stats_blob(stats)
    assert encoded == (
        struct.pack(">IBq", 1, TYPE_INT64, -9007199254740993)
        + struct.pack(">IBi", 0xFFFFFFFF, TYPE_INT32, -7)
        + struct.pack(">IBf", 42, TYPE_FLOAT, 1.5)
    )
    parsed = parse_stats_blob(encoded)
    assert list(parsed) == [1, 0xFFFFFFFF, 42]
    assert parsed[42].value == 1.5


def test_stats_blob_rejects_malformed_input():
    valid = struct.pack(">IBi", 1, TYPE_INT32, 3)
    for bad in (
        b"\x00\x00\x00\x01\x04\x01",  # truncated int32 value
        b"\x00\x00\x00\x01\x09\x00\x00\x00\x00",  # unknown wire type
        valid + b"\x01",  # trailing partial header
        valid + valid,  # duplicate id
    ):
        with pytest.raises(ProfileStatsCodecError):
            parse_stats_blob(bad)
    with pytest.raises(ProfileStatsCodecError):
        parse_stats_blob(valid, maximum=len(valid) - 1)


def test_write_stats_only_values_omits_ids():
    encoded = write_stats_only_values(
        [StatValue(999, TYPE_INT32, 5), StatValue(7, TYPE_FLOAT, -2.25)]
    )
    assert encoded == struct.pack(">Bi", TYPE_INT32, 5) + struct.pack(
        ">Bf", TYPE_FLOAT, -2.25
    )


@pytest.mark.parametrize(
    "value",
    [1 << 63, -(1 << 63) - 1],
)
def test_int64_value_range_is_enforced(value):
    with pytest.raises(ProfileStatsCodecError):
        StatValue(1, TYPE_INT64, value)


def test_stat_value_rejects_non_finite_float():
    with pytest.raises(ProfileStatsCodecError):
        StatValue(1, TYPE_FLOAT, float("nan"))


def test_parse_packed_stats_header_groups_and_exact_size():
    payload = (
        struct.pack(">hhh", 1, 2, 1)
        + struct.pack(">Iq", 100, -5)
        + struct.pack(">Ii", 200, 11)
        + struct.pack(">Ii", 300, -12)
        + struct.pack(">If", 400, 0.5)
    )
    parsed = parse_packed_stats(payload)
    assert [(stat.stat_id, stat.type, stat.value) for stat in parsed] == [
        (100, TYPE_INT64, -5),
        (200, TYPE_INT32, 11),
        (300, TYPE_INT32, -12),
        (400, TYPE_FLOAT, 0.5),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        b"\x00\x00\x00",  # short header
        struct.pack(">hhh", -1, 0, 0),  # negative count
        struct.pack(">hhh", 1, 0, 0) + struct.pack(">Iq", 1, 1) + b"\x00",  # trailing
        struct.pack(">hhh", 1, 0, 0) + struct.pack(">Ii", 1, 1),  # wrong width
        struct.pack(">hhh", 2, 0, 0) + struct.pack(">Iq", 1, 1) * 2,  # duplicate id
    ],
)
def test_parse_packed_stats_rejects_malformed_payloads(payload):
    with pytest.raises(ProfileStatsCodecError):
        parse_packed_stats(payload)


def _raw_deflate(data: bytes) -> bytes:
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    return compressor.compress(data) + compressor.flush()


def test_decompress_discards_opaque_prefix_and_inflates_raw_deflate():
    payload = struct.pack(">hhh", 0, 1, 0) + struct.pack(">Ii", 7, 3)
    compressed = b"\x09" + _raw_deflate(payload)
    assert decompress_write_payload(compressed, max_input_bytes=4096) == payload


@pytest.mark.parametrize(
    "compressed,maximum_output",
    [
        (b"", 1024),  # empty
        (b"\x01", 1024),  # no deflate payload
        (b"\x01\x00\xff", 1024),  # invalid deflate
        (b"\x01" + _raw_deflate(b"x" * 32), 16),  # output too large
    ],
)
def test_decompress_rejects_bad_or_oversized_payloads(compressed, maximum_output):
    with pytest.raises(ProfileStatsCodecError):
        decompress_write_payload(
            compressed, max_input_bytes=4096, max_output_bytes=maximum_output
        )


def test_decompress_rejects_trailing_bytes_after_deflate_stream():
    compressed = b"\x01" + _raw_deflate(b"stats") + b"trailing"
    with pytest.raises(ProfileStatsCodecError):
        decompress_write_payload(compressed, max_input_bytes=4096)
