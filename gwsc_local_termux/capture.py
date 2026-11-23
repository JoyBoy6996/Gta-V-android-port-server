"""Development-only request/response capture with aggressive redaction.

Capture is disabled by default and is always disabled when
``GWSC_ENV=production``.  Bodies are bounded, binary payloads are not stored,
and known credential fields are replaced with ``***``.
"""

import json
import logging
import os
import re
import time
import urllib.parse
from typing import Any, Dict, Optional

from starlette.datastructures import Headers, MutableHeaders

from . import config
from .logging_utils import redact_text

log = logging.getLogger("gwsc.capture")

SENSITIVE_KEYS = {
    "password",
    "passwd",
    "pwd",
    "current",
    "next",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "cookie",
    "set-cookie",
    "secret",
    "scauthtoken",
    "ticket",
    "apikey",
    "api_key",
}


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "").replace("_", "")
    return any(s.replace("-", "").replace("_", "") == lowered for s in SENSITIVE_KEYS)


def redact_mapping(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("***" if _is_sensitive_key(str(key)) else redact_mapping(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_mapping(item) for item in value]
    return value


def _safe_text(text: str, limit: int) -> Dict[str, Any]:
    return {"text": redact_text(text[:limit]), "truncated": len(text) > limit}


def redact_body(raw: bytes, content_type: str, limit: int) -> Any:
    if not raw:
        return None
    if len(raw) > limit:
        raw = raw[:limit]
    lowered = (content_type or "").lower()
    if "application/json" in lowered:
        try:
            parsed = json.loads(raw.decode("utf-8"))
            return {"json": redact_mapping(parsed)}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _safe_text(raw.decode("utf-8", errors="replace"), limit)
    if "application/x-www-form-urlencoded" in lowered:
        try:
            pairs = urllib.parse.parse_qsl(
                raw.decode("utf-8"), keep_blank_values=True
            )
            return {
                "form": {
                    key: ("***" if _is_sensitive_key(key) else redact_text(value))
                    for key, value in pairs
                }
            }
        except UnicodeDecodeError:
            return {"_binary": True, "length": len(raw)}
    if "xml" in lowered or "text/" in lowered:
        return _safe_text(raw.decode("utf-8", errors="replace"), limit)
    return {"_binary": True, "length": len(raw)}


def _allowlisted_headers(headers: Headers) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for key, value in headers.items():
        lowered = key.lower()
        if lowered in config.CAPTURE_HEADER_ALLOWLIST:
            result[lowered] = "***" if _is_sensitive_key(lowered) else redact_text(value)
    return result


def _write_capture(entry: Dict[str, Any]) -> None:
    try:
        config.CAPTURE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(
            str(config.CAPTURE_FILE),
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError:
        log.warning("Could not write request capture", exc_info=True)


async def _read_request_body(receive, max_bytes: int) -> tuple[bytes, bool]:
    """Read an ASGI request body, keeping at most ``max_bytes``.

    Returns ``(body, exceeded)``.  The stream is drained even after the cap
    so the connection can be reused; ``exceeded`` reports whether the client
    sent more than the cap.
    """
    chunks = []
    total = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        if message["type"] != "http.request":
            continue
        chunk = message.get("body", b"")
        if chunk and total < max_bytes:
            take = min(len(chunk), max_bytes - total)
            chunks.append(chunk[:take])
        total += len(chunk)
        if not message.get("more_body", False):
            break
    return b"".join(chunks), total > max_bytes


class RequestIDMiddleware:
    """Attach a correlation ID to every request and response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        supplied = headers.get("x-request-id") or ""
        safe = re.sub(r"[^A-Za-z0-9._-]", "", supplied)[:64]
        request_id = safe or os.urandom(16).hex()
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        await self.app(scope, receive, send_wrapper)


class AccessLogMiddleware:
    """Log route, status and duration without query strings or credentials."""

    def __init__(self, app):
        self.app = app
        self.log = logging.getLogger("gwsc.access")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        status_code = 500

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.monotonic() - started) * 1000, 2)
            request_id = scope.get("state", {}).get("request_id", "-")
            client = scope.get("client") or ("-", 0)
            self.log.info(
                "%s %s %s %sms client=%s request_id=%s",
                scope.get("method"),
                scope.get("path"),
                status_code,
                duration_ms,
                client[0],
                request_id,
            )


def _body_limit_for_path(path: str) -> int:
    """Cloud binaries have their own (larger) upstream upload boundary."""
    if any(
        path.startswith(prefix) for prefix in config.CLOUD_UPLOAD_PATH_PREFIXES
    ):
        return config.OBJECT_MAX_BYTES
    return config.MAX_BODY_BYTES


class BodyLimitMiddleware:
    """Reject oversized request bodies before they reach a route."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = _body_limit_for_path(scope.get("path", ""))
        headers = Headers(scope=scope)
        content_length = headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > limit:
                    await self._reject(scope, send)
                    return
            except ValueError:
                await self._reject(scope, send)
                return

        body, exceeded = await _read_request_body(receive, limit)
        if exceeded:
            await self._reject(scope, send)
            return

        replayed = False

        async def replay_receive():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _reject(scope, send):
        request_id = scope.get("state", {}).get("request_id")
        payload = json.dumps(
            {
                "error": "request_too_large",
                "message": "Request body exceeds the configured limit",
                "detail": {"error": "request_too_large"},
            }
        ).encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode("ascii")),
        ]
        if request_id:
            headers.append((b"x-request-id", request_id.encode("ascii")))
        await send({"type": "http.response.start", "status": 413, "headers": headers})
        await send({"type": "http.response.body", "body": payload})


class CaptureMiddleware:
    """Record sanitized request/response pairs to a local JSONL file."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not config.CAPTURE_ENABLED:
            await self.app(scope, receive, send)
            return

        started = time.monotonic()
        headers = Headers(scope=scope)
        content_type = headers.get("content-type", "")
        request_body, body_exceeded = await _read_request_body(
            receive, config.MAX_BODY_BYTES
        )
        captured_request_body = request_body[: config.CAPTURE_MAX_BODY]
        request_body_truncated = (
            body_exceeded or len(request_body) > config.CAPTURE_MAX_BODY
        )

        replayed = False

        async def replay_receive():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": request_body, "more_body": False}
            return {"type": "http.disconnect"}

        response_status: Optional[int] = None
        response_headers: Dict[str, str] = {}
        response_chunks = []
        response_size = 0
        streaming = False

        async def send_wrapper(message):
            nonlocal response_status, response_headers, response_size, streaming
            if message["type"] == "http.response.start":
                response_status = message["status"]
                header_headers = Headers(raw=message.get("headers", []))
                response_headers = _allowlisted_headers(header_headers)
                streaming = "text/event-stream" in header_headers.get(
                    "content-type", ""
                )
            elif message["type"] == "http.response.body" and not streaming:
                chunk = message.get("body", b"")
                response_size += len(chunk)
                if response_size <= config.CAPTURE_MAX_BODY:
                    response_chunks.append(chunk)
            await send(message)

        error: Optional[str] = None
        try:
            await self.app(scope, replay_receive, send_wrapper)
        except Exception as exc:  # re-raised after recording
            error = redact_text(f"{type(exc).__name__}: {exc}")
            raise
        finally:
            entry = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "request_id": scope.get("state", {}).get("request_id"),
                "method": scope.get("method"),
                "path": scope.get("path"),
                "query": redact_text(
                    (scope.get("query_string") or b"").decode("utf-8", errors="replace")
                ),
                "client": scope.get("client"),
                "request_headers": _allowlisted_headers(headers),
                "request_body": redact_body(
                    captured_request_body, content_type, config.CAPTURE_MAX_BODY
                ),
                "request_body_truncated": request_body_truncated,
                "response_status": response_status,
                "response_headers": response_headers,
                "response_body": (
                    None
                    if streaming
                    else redact_body(
                        b"".join(response_chunks),
                        response_headers.get("content-type", ""),
                        config.CAPTURE_MAX_BODY,
                    )
                ),
                "streaming": streaming,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
                "error": error,
            }
            _write_capture(entry)
