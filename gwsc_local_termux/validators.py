"""Input validation shared by account and social routes."""

import re
from typing import Optional

from . import config

# Deliberately conservative: the client may accept more, but these values
# keep stored data predictable.  Marked UNVERIFIED against the Android UI.
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
# Nickname length is configurable, so only the character set is fixed here.
_NICKNAME_RE = re.compile(r"^[A-Za-z0-9_\- .]+$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def normalise_email(email: str) -> str:
    return email.strip().lower()


def validate_email(email: str) -> Optional[str]:
    normalised = normalise_email(email)
    if not normalised or len(normalised) > 320 or not _EMAIL_RE.match(normalised):
        return None
    return normalised


def validate_nickname(nickname: str) -> Optional[str]:
    value = nickname.strip()
    if not (
        config.NICKNAME_MIN_LENGTH <= len(value) <= config.NICKNAME_MAX_LENGTH
    ):
        return None
    if not _NICKNAME_RE.match(value):
        return None
    return value


def validate_password(password: str) -> Optional[str]:
    if not isinstance(password, str):
        return "Password must be a string"
    if len(password) < config.PASSWORD_MIN_LENGTH:
        return f"Password must be at least {config.PASSWORD_MIN_LENGTH} characters"
    if len(password) > config.PASSWORD_MAX_LENGTH:
        return f"Password must be at most {config.PASSWORD_MAX_LENGTH} characters"
    return None


def clean_message_body(body: str) -> Optional[str]:
    if not isinstance(body, str):
        return None
    value = body.strip()
    if not value or len(value) > config.MAX_MESSAGE_LENGTH:
        return None
    if _CONTROL_RE.search(value):
        return None
    return value
