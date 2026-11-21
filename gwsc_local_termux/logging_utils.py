"""Logging helpers that keep credentials out of log files."""

import logging
import re

# Values after a sensitive key are replaced with ``***``.
_KEY_VALUE_RE = re.compile(
    r"(?i)(password|passwd|pwd|token|authorization|cookie|secret|"
    r"scauthtoken|ticket|api[_-]?key)(\s*[:=]\s*)([\"']?)([^\"'\s,&}]+)"
)
# Bearer/basic credentials inside free-form messages.
_AUTH_HEADER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]+")
# Long opaque token-looking strings.
_OPAQUE_RE = re.compile(r"\b(?:gwsc_|gwsc_ticket_)[A-Za-z0-9._~\-]{12,}\b")


def redact_text(text: str) -> str:
    if not isinstance(text, str):
        return text
    text = _AUTH_HEADER_RE.sub(lambda m: f"{m.group(1)} ***", text)
    text = _KEY_VALUE_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}***", text)
    text = _OPAQUE_RE.sub("***", text)
    return text


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            record.msg = redact_text(message)
            record.args = ()
            if record.exc_info:
                formatter = logging.Formatter()
                record.exc_text = redact_text(formatter.formatException(record.exc_info))
                record.exc_info = None
        except Exception:
            # Never let a logging failure break request handling.
            record.msg = "<log message redacted>"
            record.args = ()
        return True


def install_redaction_filter(logger: logging.Logger) -> None:
    for handler in logger.handlers:
        handler.addFilter(RedactionFilter())
