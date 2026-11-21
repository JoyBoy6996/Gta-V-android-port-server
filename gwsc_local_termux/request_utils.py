"""Request helpers shared by routes."""

from typing import Optional

from fastapi import Request

from . import config


def client_ip(request: Request) -> str:
    if config.TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
    return request.client.host if request.client else "unknown"


def rate_key(request: Request, action: str, identifier: Optional[str] = None) -> str:
    parts = [action, client_ip(request)]
    if identifier:
        parts.append(identifier)
    return ":".join(parts)
