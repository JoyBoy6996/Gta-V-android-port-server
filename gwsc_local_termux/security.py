"""Password, token and rate-limit primitives.

Passwords are hashed with PBKDF2-HMAC-SHA256 (a modern, dependency-free
scheme available in the Android/Termux Python build).  Legacy hashes from
the original prototype use ``salt:hex`` and are accepted once, then upgraded.
Tokens are random opaque values; only their SHA-256 hashes are stored.
"""

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple

from . import config

PASSWORD_SCHEME = "pbkdf2_sha256"
_LEGACY_ITERATIONS = 100_000


def _normalise_password(password: str) -> bytes:
    # Do not strip passwords: whitespace is significant.
    return password.encode("utf-8")


def hash_password(password: str, iterations: Optional[int] = None) -> str:
    """Return a versioned PBKDF2 hash string."""
    iterations = iterations or config.PASSWORD_PBKDF2_ITERATIONS
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", _normalise_password(password), salt.encode("ascii"), iterations
    )
    return f"{PASSWORD_SCHEME}${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Verify a password against a current or legacy hash.

    Returns ``False`` for malformed hashes instead of raising, so callers can
    always answer with a generic authentication error.
    """
    if not stored:
        return False
    try:
        if stored.startswith(PASSWORD_SCHEME + "$"):
            _scheme, iterations_raw, salt, expected_hex = stored.split("$", 3)
            iterations = int(iterations_raw)
            digest = hashlib.pbkdf2_hmac(
                "sha256", _normalise_password(password), salt.encode("ascii"), iterations
            )
            return hmac.compare_digest(digest.hex(), expected_hex)
        # Legacy prototype format: <salt_hex>:<digest_hex>
        if ":" in stored:
            salt, expected_hex = stored.split(":", 1)
            digest = hashlib.pbkdf2_hmac(
                "sha256",
                _normalise_password(password),
                salt.encode("ascii"),
                _LEGACY_ITERATIONS,
            )
            return hmac.compare_digest(digest.hex(), expected_hex)
    except (ValueError, TypeError):
        return False
    return False


def password_needs_rehash(stored: str) -> bool:
    if not stored or not stored.startswith(PASSWORD_SCHEME + "$"):
        return True
    try:
        _scheme, iterations_raw, _salt, _digest = stored.split("$", 3)
        return int(iterations_raw) < config.PASSWORD_PBKDF2_ITERATIONS
    except (ValueError, TypeError):
        return True


def hash_token(token: str) -> str:
    """Hash a bearer token before storage or lookup."""
    payload = (config.TOKEN_HASH_PEPPER + token).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def generate_token(prefix: str = "gwsc_") -> str:
    return prefix + secrets.token_urlsafe(32)


def generate_ticket() -> str:
    # Private server ticket, not a Rockstar credential.
    return "gwsc_ticket_" + secrets.token_urlsafe(32)


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


class RateLimiter:
    """Process-local sliding-window limiter.

    This is intentionally lightweight: the project does not add Redis just
    for rate limiting.  In a multi-worker deployment each worker enforces
    its own window, so limits should be set with that in mind.
    """

    def __init__(self) -> None:
        self._events: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: int) -> Tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)``."""
        if limit <= 0 or window_seconds <= 0:
            return True, 0
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            bucket = self._events[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(1, int(window_seconds - (now - bucket[0])))
                return False, retry_after
            bucket.append(now)
            # Opportunistically drop empty buckets to bound memory.
            if len(self._events) > 10_000:
                for stale_key in [k for k, v in self._events.items() if not v]:
                    self._events.pop(stale_key, None)
        return True, 0

    def reset(self, key: Optional[str] = None) -> None:
        with self._lock:
            if key is None:
                self._events.clear()
            else:
                self._events.pop(key, None)


rate_limiter = RateLimiter()
