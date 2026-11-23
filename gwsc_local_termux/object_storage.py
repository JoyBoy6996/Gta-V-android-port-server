"""Account-scoped object storage for LSO cloud binaries.

``SOURCE_CONFIRMED``: upstream LSO stores save files, news images and UGC
binaries in S3.  This module is the ``ADAPTED`` FastAPI side of that design:
callers build keys from authenticated account ids, every key component is
re-validated here, and credentials are read from the environment only.  No
credential value is logged, serialised or included in an exception message.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Tuple

from . import config

_KEY_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_KEY_PARTS = 16
_MAX_KEY_LENGTH = 512


class ObjectStorageError(RuntimeError):
    """Base class for object-storage failures that routes can translate."""


class ObjectStorageConfigurationError(ObjectStorageError):
    """The backend configuration is missing, invalid, or unsupported."""


class InvalidObjectKeyError(ObjectStorageError, ValueError):
    """A key component would escape its namespace or is malformed."""


class ObjectNotFoundError(ObjectStorageError):
    """The requested object does not exist."""


@dataclass(frozen=True)
class ObjectInfo:
    """Non-secret metadata returned after a successful write or lookup."""

    key: str
    size: int
    sha256: str


def validate_key(parts: Sequence[str]) -> Tuple[str, ...]:
    """Return a canonical tuple of key components or raise.

    LSO keys are simple path segments.  We allow only a strict ASCII subset and
    ban ``.``/``..`` so no component can contain a separator, traversal token or
    XML metacharacter.  The total length is bounded for both filesystem and S3
    backends.
    """
    if not parts:
        raise InvalidObjectKeyError("Object key is empty")
    if len(parts) > _MAX_KEY_PARTS:
        raise InvalidObjectKeyError("Object key has too many components")
    validated: list[str] = []
    for part in parts:
        if not isinstance(part, str):
            raise InvalidObjectKeyError("Object key components must be strings")
        if part in (".", "..") or not _KEY_PART_RE.fullmatch(part):
            raise InvalidObjectKeyError("Invalid object key component")
        validated.append(part)
    key = "/".join(validated)
    if len(key) > _MAX_KEY_LENGTH:
        raise InvalidObjectKeyError("Object key is too long")
    return tuple(validated)


def account_key(account_id: object, category: str, *parts: str) -> Tuple[str, ...]:
    """Build an account-bound key prefix.

    The account id comes from authenticated request state, never from a path
    value or client-supplied field.  ``category`` separates save files, news
    assets and UGC objects so one class of content cannot overwrite another.
    """
    try:
        resolved_id = int(account_id)
    except (TypeError, ValueError):
        raise InvalidObjectKeyError("Invalid account id for object key")
    if resolved_id <= 0:
        raise InvalidObjectKeyError("Invalid account id for object key")
    return validate_key(("accounts", str(resolved_id), category, *parts))


def _require_bytes(data: object, max_bytes: int) -> bytes:
    if isinstance(data, str):
        raise TypeError("Object data must be bytes")
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("Object data must be bytes")
    raw = bytes(data)
    if len(raw) > max_bytes:
        raise ObjectStorageError("Object exceeds the configured size limit")
    return raw


class ObjectStore:
    """Minimal interface shared by the local and S3-compatible backends."""

    def put(
        self, parts: Sequence[str], data: bytes, *, content_type: Optional[str] = None
    ) -> ObjectInfo:
        raise NotImplementedError

    def get(self, parts: Sequence[str]) -> bytes:
        raise NotImplementedError

    def stat(self, parts: Sequence[str]) -> ObjectInfo:
        raise NotImplementedError

    def delete(self, parts: Sequence[str]) -> None:
        raise NotImplementedError


class LocalObjectStore(ObjectStore):
    """Private filesystem backend; the default for self-hosted deployments."""

    def __init__(self, root: Path, *, max_object_bytes: int) -> None:
        if max_object_bytes <= 0:
            raise ObjectStorageConfigurationError(
                "Object size limit must be a positive integer"
            )
        self.root = Path(root)
        self.max_object_bytes = max_object_bytes
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def __repr__(self) -> str:
        return f"LocalObjectStore(root={self.root!s}, max_bytes={self.max_object_bytes})"

    def _path(self, parts: Sequence[str]) -> Path:
        validated = validate_key(parts)
        path = self.root.joinpath(*validated)
        root = self.root.resolve(strict=False)
        candidate = path.resolve(strict=False)
        try:
            if os.path.commonpath((str(root), str(candidate))) != str(root):
                raise InvalidObjectKeyError("Object key escapes the storage root")
        except ValueError:
            raise InvalidObjectKeyError("Object key escapes the storage root")
        return path

    def put(
        self, parts: Sequence[str], data: bytes, *, content_type: Optional[str] = None
    ) -> ObjectInfo:
        raw = _require_bytes(data, self.max_object_bytes)
        path = self._path(parts)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=".gwsc-put-", dir=str(path.parent))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
        return ObjectInfo(
            key="/".join(validate_key(parts)),
            size=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
        )

    def get(self, parts: Sequence[str]) -> bytes:
        path = self._path(parts)
        try:
            info = path.stat()
        except FileNotFoundError:
            raise ObjectNotFoundError("Object does not exist")
        if not stat.S_ISREG(info.st_mode):
            raise ObjectNotFoundError("Object does not exist")
        if info.st_size > self.max_object_bytes:
            raise ObjectStorageError("Object exceeds the configured size limit")
        return path.read_bytes()

    def stat(self, parts: Sequence[str]) -> ObjectInfo:
        path = self._path(parts)
        try:
            info = path.stat()
        except FileNotFoundError:
            raise ObjectNotFoundError("Object does not exist")
        if not stat.S_ISREG(info.st_mode):
            raise ObjectNotFoundError("Object does not exist")
        if info.st_size > self.max_object_bytes:
            raise ObjectStorageError("Object exceeds the configured size limit")
        key = "/".join(validate_key(parts))
        return ObjectInfo(
            key=key,
            size=info.st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )

    def delete(self, parts: Sequence[str]) -> None:
        path = self._path(parts)
        try:
            path.unlink()
        except FileNotFoundError:
            raise ObjectNotFoundError("Object does not exist")


class S3ObjectStore(ObjectStore):
    """S3-compatible backend using the optional ``boto3`` dependency.

    Credentials are accepted only from configuration and are never included in
    ``repr``, logs or errors.  An endpoint is required so a misconfigured
    private server cannot silently write to a public AWS bucket.
    """

    def __init__(
        self,
        *,
        endpoint_url: str,
        bucket: str,
        region: str,
        access_key: str,
        secret_key: str,
        max_object_bytes: int,
    ) -> None:
        if max_object_bytes <= 0:
            raise ObjectStorageConfigurationError(
                "Object size limit must be a positive integer"
            )
        if not endpoint_url:
            raise ObjectStorageConfigurationError(
                "S3 object storage requires GWSC_OBJECT_STORAGE_ENDPOINT"
            )
        parsed = urllib.parse.urlsplit(endpoint_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ObjectStorageConfigurationError(
                "S3 object storage endpoint must be an http(s) URL"
            )
        if parsed.username or parsed.password:
            raise ObjectStorageConfigurationError(
                "S3 object storage endpoint must not contain credentials"
            )
        if not bucket or not _KEY_PART_RE.fullmatch(bucket):
            raise ObjectStorageConfigurationError(
                "S3 object storage requires a valid bucket name"
            )
        if not region or not _KEY_PART_RE.fullmatch(region):
            raise ObjectStorageConfigurationError(
                "S3 object storage requires a valid region"
            )
        if not access_key or not secret_key:
            raise ObjectStorageConfigurationError(
                "S3 object storage requires access key and secret key environment variables"
            )
        self.endpoint_url = endpoint_url
        self.bucket = bucket
        self.region = region
        self._access_key = access_key
        self._secret_key = secret_key
        self.max_object_bytes = max_object_bytes

    def __repr__(self) -> str:
        return (
            f"S3ObjectStore(endpoint={self.endpoint_url!r}, bucket={self.bucket!r}, "
            f"region={self.region!r}, max_bytes={self.max_object_bytes})"
        )

    def _client(self):
        try:
            import boto3  # type: ignore[import-not-found]
            from botocore.exceptions import ClientError  # type: ignore[import-not-found]
        except ImportError:
            raise ObjectStorageConfigurationError(
                "S3 object storage requires the optional boto3 package"
            )
        return boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            region_name=self.region,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
        ), ClientError

    @staticmethod
    def _translate(error: Exception, client_error_type: type) -> ObjectStorageError:
        if isinstance(error, client_error_type):
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in ("404", "NoSuchKey", "NotFound"):
                return ObjectNotFoundError("Object does not exist")
            return ObjectStorageError(f"S3 object storage failed ({code or 'error'})")
        return ObjectStorageError("S3 object storage failed")

    @staticmethod
    def _key(parts: Sequence[str]) -> str:
        return "/".join(validate_key(parts))

    def put(
        self, parts: Sequence[str], data: bytes, *, content_type: Optional[str] = None
    ) -> ObjectInfo:
        raw = _require_bytes(data, self.max_object_bytes)
        client, client_error_type = self._client()
        arguments = {
            "Bucket": self.bucket,
            "Key": self._key(parts),
            "Body": raw,
        }
        if content_type:
            arguments["ContentType"] = content_type
        try:
            client.put_object(**arguments)
        except client_error_type as exc:
            raise self._translate(exc, client_error_type) from None
        return ObjectInfo(
            key=arguments["Key"],
            size=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
        )

    def get(self, parts: Sequence[str]) -> bytes:
        client, client_error_type = self._client()
        try:
            response = client.get_object(Bucket=self.bucket, Key=self._key(parts))
            raw = response["Body"].read()
        except client_error_type as exc:
            raise self._translate(exc, client_error_type) from None
        if len(raw) > self.max_object_bytes:
            raise ObjectStorageError("Object exceeds the configured size limit")
        return raw

    def stat(self, parts: Sequence[str]) -> ObjectInfo:
        client, client_error_type = self._client()
        try:
            response = client.head_object(Bucket=self.bucket, Key=self._key(parts))
        except client_error_type as exc:
            raise self._translate(exc, client_error_type) from None
        size = int(response.get("ContentLength", 0))
        if size > self.max_object_bytes:
            raise ObjectStorageError("Object exceeds the configured size limit")
        return ObjectInfo(key=self._key(parts), size=size, sha256="")

    def delete(self, parts: Sequence[str]) -> None:
        client, client_error_type = self._client()
        try:
            client.delete_object(Bucket=self.bucket, Key=self._key(parts))
        except client_error_type as exc:
            raise self._translate(exc, client_error_type) from None


def get_object_store(*, profile: str = "default") -> ObjectStore:
    """Build the configured backend from current config values.

    ``profile='ugc'`` selects the separate UGC bucket/credentials used by
    upstream; ``profile='news'`` selects the news bucket with the main
    credentials.  Tests can replace config values between requests; no global
    client is cached.  Unsupported backends fail closed rather than falling
    back to a different storage location.
    """
    backend = config.OBJECT_STORAGE_BACKEND
    maximum = config.OBJECT_MAX_BYTES
    if backend == "local":
        return LocalObjectStore(config.OBJECT_STORAGE_DIR, max_object_bytes=maximum)
    if backend == "s3":
        if profile == "ugc":
            endpoint = config.UGC_OBJECT_STORAGE_ENDPOINT
            bucket = config.UGC_OBJECT_STORAGE_BUCKET
            access_key = config.UGC_OBJECT_STORAGE_ACCESS_KEY
            secret_key = config.UGC_OBJECT_STORAGE_SECRET_KEY
        elif profile == "news":
            endpoint = config.OBJECT_STORAGE_ENDPOINT
            bucket = config.NEWS_OBJECT_STORAGE_BUCKET
            access_key = config.OBJECT_STORAGE_ACCESS_KEY
            secret_key = config.OBJECT_STORAGE_SECRET_KEY
        elif profile == "default":
            endpoint = config.OBJECT_STORAGE_ENDPOINT
            bucket = config.OBJECT_STORAGE_BUCKET
            access_key = config.OBJECT_STORAGE_ACCESS_KEY
            secret_key = config.OBJECT_STORAGE_SECRET_KEY
        else:
            raise ObjectStorageConfigurationError("Unknown object-storage profile")
        return S3ObjectStore(
            endpoint_url=endpoint,
            bucket=bucket,
            region=config.OBJECT_STORAGE_REGION,
            access_key=access_key,
            secret_key=secret_key,
            max_object_bytes=maximum,
        )
    raise ObjectStorageConfigurationError(
        "GWSC_OBJECT_STORAGE_BACKEND must be 'local' or 's3'"
    )
