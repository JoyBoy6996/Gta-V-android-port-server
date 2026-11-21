"""Consistent API error helpers.

The Android client's exact error parser has not been captured.  To avoid
depending on FastAPI's default ``{"detail": ...}`` wrapper we return a
stable object that contains both top-level ``error``/``message`` fields and
the same object under ``detail`` for backward compatibility.
"""

from typing import Any, Dict, Optional

from fastapi import HTTPException


def api_error(
    status_code: int,
    error: str,
    message: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    **extra: Any,
) -> HTTPException:
    detail: Dict[str, Any] = {"error": error, "message": message}
    if extra:
        detail.update(extra)
    return HTTPException(status_code=status_code, detail=detail, headers=headers)


def error_payload(detail: Any) -> Dict[str, Any]:
    """Normalise an HTTPException detail into the public error object."""
    if isinstance(detail, dict):
        payload = dict(detail)
        payload.setdefault("error", "error")
        payload.setdefault("message", "Request failed")
        payload["detail"] = dict(detail)
        return payload
    return {
        "error": "error",
        "message": str(detail),
        "detail": detail,
    }
