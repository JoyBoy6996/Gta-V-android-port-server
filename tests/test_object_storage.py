"""Tests for the account-scoped cloud object-storage adapter."""

import hashlib
import stat

import pytest

from gwsc_local_termux import config, object_storage


def test_validate_key_accepts_safe_segments():
    assert object_storage.validate_key(("accounts", "12", "saves", "slot1.bin")) == (
        "accounts",
        "12",
        "saves",
        "slot1.bin",
    )


@pytest.mark.parametrize(
    "parts",
    [
        (),
        ("",),
        ("..",),
        ("accounts/12",),
        ("slot 1",),
        ("slot<1>",),
        ("a" * 129,),
    ],
)
def test_validate_key_rejects_unsafe_segments(parts):
    with pytest.raises(object_storage.InvalidObjectKeyError):
        object_storage.validate_key(parts)


def test_account_key_is_bound_to_authenticated_id():
    assert object_storage.account_key(42, "saves", "slot1") == (
        "accounts",
        "42",
        "saves",
        "slot1",
    )
    for bad_id in (0, -1, "not-a-number", None):
        with pytest.raises(object_storage.InvalidObjectKeyError):
            object_storage.account_key(bad_id, "saves", "slot1")
    with pytest.raises(object_storage.InvalidObjectKeyError):
        object_storage.account_key(1, "../saves")


def test_local_store_round_trip_is_byte_exact(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OBJECT_STORAGE_BACKEND", "local")
    monkeypatch.setattr(config, "OBJECT_STORAGE_DIR", tmp_path / "objects")
    store = object_storage.get_object_store()
    key = object_storage.account_key(7, "saves", "slot1.bin")
    payload = bytes(range(256)) + b"\x00\xff\n"

    info = store.put(key, payload)

    assert info.size == len(payload)
    assert info.sha256 == hashlib.sha256(payload).hexdigest()
    assert store.get(key) == payload
    assert store.stat(key) == info
    path = (tmp_path / "objects").joinpath(*key)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    store.delete(key)
    with pytest.raises(object_storage.ObjectNotFoundError):
        store.get(key)
    with pytest.raises(object_storage.ObjectNotFoundError):
        store.delete(key)


def test_local_store_rejects_oversized_objects(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OBJECT_MAX_BYTES", 4)
    store = object_storage.LocalObjectStore(tmp_path, max_object_bytes=4)
    with pytest.raises(object_storage.ObjectStorageError):
        store.put(("accounts", "1", "saves", "a"), b"12345")
    with pytest.raises(TypeError):
        store.put(("accounts", "1", "saves", "a"), "not bytes")


def test_local_store_rejects_traversal(tmp_path):
    store = object_storage.LocalObjectStore(tmp_path, max_object_bytes=128)
    with pytest.raises(object_storage.InvalidObjectKeyError):
        store.get(("..", "secret"))
    assert not (tmp_path.parent / "secret").exists()


@pytest.mark.parametrize(
    "backend", ["", "unknown", "LOCAL", "s3", "S3"]
)
def test_unsupported_or_partial_s3_config_fails_closed(
    tmp_path, monkeypatch, backend
):
    monkeypatch.setattr(config, "OBJECT_STORAGE_BACKEND", backend)
    monkeypatch.setattr(config, "OBJECT_STORAGE_DIR", tmp_path / "objects")
    monkeypatch.setattr(config, "OBJECT_STORAGE_ENDPOINT", "")
    monkeypatch.setattr(config, "OBJECT_STORAGE_BUCKET", "gwsc")
    monkeypatch.setattr(config, "OBJECT_STORAGE_REGION", "us-east-1")
    monkeypatch.setattr(config, "OBJECT_STORAGE_ACCESS_KEY", "")
    monkeypatch.setattr(config, "OBJECT_STORAGE_SECRET_KEY", "")
    with pytest.raises(object_storage.ObjectStorageConfigurationError):
        object_storage.get_object_store()


def test_s3_profile_uses_separate_bucket_and_endpoint(monkeypatch):
    monkeypatch.setattr(config, "OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.setattr(config, "OBJECT_STORAGE_ENDPOINT", "https://main.invalid")
    monkeypatch.setattr(config, "OBJECT_STORAGE_BUCKET", "main-bucket")
    monkeypatch.setattr(config, "OBJECT_STORAGE_ACCESS_KEY", "main-id")
    monkeypatch.setattr(config, "OBJECT_STORAGE_SECRET_KEY", "main-secret")
    monkeypatch.setattr(config, "UGC_OBJECT_STORAGE_ENDPOINT", "https://ugc.invalid")
    monkeypatch.setattr(config, "UGC_OBJECT_STORAGE_BUCKET", "ugc-bucket")
    monkeypatch.setattr(config, "UGC_OBJECT_STORAGE_ACCESS_KEY", "ugc-id")
    monkeypatch.setattr(config, "UGC_OBJECT_STORAGE_SECRET_KEY", "ugc-secret")
    monkeypatch.setattr(config, "NEWS_OBJECT_STORAGE_BUCKET", "news-bucket")

    ugc = object_storage.get_object_store(profile="ugc")
    news = object_storage.get_object_store(profile="news")
    assert ugc.endpoint_url == "https://ugc.invalid"
    assert ugc.bucket == "ugc-bucket"
    assert news.endpoint_url == "https://main.invalid"
    assert news.bucket == "news-bucket"
    assert "ugc-secret" not in repr(ugc)
    assert "main-secret" not in repr(news)


def test_s3_config_never_exposes_credentials(monkeypatch):
    monkeypatch.setattr(config, "OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.setattr(config, "OBJECT_STORAGE_ENDPOINT", "https://s3.example.invalid")
    monkeypatch.setattr(config, "OBJECT_STORAGE_BUCKET", "gwsc")
    monkeypatch.setattr(config, "OBJECT_STORAGE_REGION", "us-east-1")
    monkeypatch.setattr(config, "OBJECT_STORAGE_ACCESS_KEY", "public-id")
    monkeypatch.setattr(config, "OBJECT_STORAGE_SECRET_KEY", "super-secret-value")

    store = object_storage.get_object_store()
    rendered = repr(store)
    assert "super-secret-value" not in rendered
    assert "public-id" not in rendered
    assert "s3.example.invalid" in rendered
