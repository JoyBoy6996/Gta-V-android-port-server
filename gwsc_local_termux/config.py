"""Central configuration for the GWSC private backend.

Every value can be overridden with an environment variable.  Values that
affect the wire protocol are intentionally conservative: when the Android
client's behaviour has not been captured, the code documents the value as
UNVERIFIED instead of pretending it is a Rockstar-compatible contract.
"""

import os
from pathlib import Path

from .xml_protocol import service_hosts

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _env_list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [part.strip() for part in raw.split(",") if part.strip()]


# ---------------------------------------------------------------------------
# Environment / identity
# ---------------------------------------------------------------------------
ENVIRONMENT = os.getenv("GWSC_ENV", "development").strip().lower()
IS_PRODUCTION = ENVIRONMENT in ("production", "prod")
IS_RENDER = bool(os.getenv("RENDER") or os.getenv("PORT"))

SERVER_NAME = os.getenv("GWSC_SERVER_NAME", "Unknown Beaches Social Club")
SERVER_VERSION = os.getenv("GWSC_SERVER_VERSION", "1.0.0")
SERVER_HOST = os.getenv("GWSC_HOST", "0.0.0.0")

# Render (and most PaaS providers) inject PORT and terminate TLS at the edge.
PORT_ENV = os.getenv("PORT")
SERVER_PORT = int(PORT_ENV or os.getenv("GWSC_PORT", "8443"))

RELAY_HOST = os.getenv("GWSC_RELAY_HOST", "0.0.0.0")
RELAY_PORT = int(os.getenv("GWSC_RELAY_PORT", "6672"))
# Address advertised by GeoLocation.asmx/GetRelayServers.  It must be the
# publicly reachable UDP relay, not the internal listener address.
RELAY_PUBLIC_ADDRESS = os.getenv("GWSC_RELAY_PUBLIC_ADDRESS", "").strip()
RELAY_PUBLIC_PORT = _env_int("GWSC_RELAY_PUBLIC_PORT", RELAY_PORT)

# Public hostname used when advertising matchmaking sessions to remote
# clients.  Empty means "use the address the client connected from".
PUBLIC_HOST = os.getenv("GWSC_PUBLIC_HOST", "").strip()

# ---------------------------------------------------------------------------
# TLS
# ---------------------------------------------------------------------------
SSL_ENABLED = _env_bool(
    "GWSC_SSL_ENABLED", default=not IS_RENDER
)
SSL_CERT = BASE_DIR / os.getenv("GWSC_SSL_CERT", "cert.pem")
SSL_KEY = BASE_DIR / os.getenv("GWSC_SSL_KEY", "key.pem")

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
_data_dir = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / os.getenv("GWSC_DB_NAME", "gwsc.db")

LOG_DIR = Path(os.getenv("GWSC_LOG_DIR", str(BASE_DIR / "logs")))
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "gwsc.log"

BACKUP_DIR = Path(os.getenv("GWSC_BACKUP_DIR", str(BASE_DIR / "backups")))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Accounts and sessions
# ---------------------------------------------------------------------------
REGISTRATION_ENABLED = _env_bool("GWSC_REGISTRATION", default=True)
PASSWORD_MIN_LENGTH = _env_int("GWSC_PASSWORD_MIN_LENGTH", 8)
PASSWORD_MAX_LENGTH = _env_int("GWSC_PASSWORD_MAX_LENGTH", 128)
NICKNAME_MIN_LENGTH = _env_int("GWSC_NICKNAME_MIN_LENGTH", 3)
NICKNAME_MAX_LENGTH = _env_int("GWSC_NICKNAME_MAX_LENGTH", 30)

TOKEN_EXPIRY_SECONDS = _env_int("GWSC_TOKEN_EXPIRY", 86400)
TOKEN_LAST_USED_UPDATE_SECONDS = _env_int("GWSC_TOKEN_LAST_USED_UPDATE", 60)
# Optional server-side pepper for token hashes.  Tokens are always hashed
# with SHA-256 before storage; the pepper adds defence in depth if the
# database leaks.  Keep it stable for the lifetime of the deployment.
TOKEN_HASH_PEPPER = os.getenv("GWSC_TOKEN_PEPPER", "")

# Password hashing work factor (PBKDF2-HMAC-SHA256).  Legacy hashes using
# the old ``salt:hex`` format are still accepted and transparently upgraded
# on the next successful login.
PASSWORD_PBKDF2_ITERATIONS = _env_int("GWSC_PBKDF2_ITERATIONS", 600_000)

# ---------------------------------------------------------------------------
# Presence
# ---------------------------------------------------------------------------
PRESENCE_TIMEOUT_SECONDS = _env_int("GWSC_PRESENCE_TIMEOUT", 300)
PRESENCE_TOUCH_INTERVAL_SECONDS = _env_int("GWSC_PRESENCE_TOUCH_INTERVAL", 30)

# ---------------------------------------------------------------------------
# Social
# ---------------------------------------------------------------------------
MAX_MESSAGE_LENGTH = _env_int("GWSC_MAX_MESSAGE_LENGTH", 2000)
MAX_GROUP_MEMBERS = _env_int("GWSC_MAX_GROUP_MEMBERS", 50)
MAX_CONVERSATIONS_PER_REQUEST = _env_int("GWSC_MAX_CONVERSATIONS_PER_REQUEST", 50)
MAX_MESSAGES_PAGE_SIZE = _env_int("GWSC_MAX_MESSAGES_PAGE_SIZE", 100)
SSE_MAX_CONNECTIONS_PER_USER = _env_int("GWSC_SSE_MAX_CONNECTIONS", 5)
SSE_HEARTBEAT_SECONDS = _env_int("GWSC_SSE_HEARTBEAT", 15)

# ---------------------------------------------------------------------------
# Native ROS / matchmaking
# ---------------------------------------------------------------------------
TICKET_EXPIRY_SECONDS = _env_int("GWSC_TICKET_EXPIRY", 3600)

# Optional native service-host redirect.  SOURCE_CONFIRMED: the installed
# launcher already passes ``-rosHost=<host>`` to the game, so this is an opt-in
# override for deployments where that value is wrong or cannot be changed.  A
# bare ``host[:port]`` is emitted as ``<S ep=".." h=".."/>`` in CreateTicket
# responses; '*' matches every native ROS endpoint.  Empty keeps the current
# behaviour (no Services block).  Invalid values fail at startup.
NATIVE_SERVICE_HOST = os.getenv("GWSC_NATIVE_SERVICE_HOST", "").strip()
NATIVE_SERVICE_EP = os.getenv("GWSC_NATIVE_SERVICE_EP", "*").strip()
NATIVE_SERVICE_HOSTS = service_hosts(NATIVE_SERVICE_HOST, NATIVE_SERVICE_EP)

MAX_TICKETS_PER_ACCOUNT = _env_int("GWSC_MAX_TICKETS_PER_ACCOUNT", 10)
# Launch tickets are capped per service so a burst of launches cannot evict
# ROS tickets used by a running game session.
MAX_LAUNCH_TICKETS_PER_ACCOUNT = _env_int("GWSC_MAX_LAUNCH_TICKETS_PER_ACCOUNT", 3)
SESSION_TIMEOUT_SECONDS = _env_int("GWSC_SESSION_TIMEOUT", 300)
MAX_SESSIONS_PER_HOST = _env_int("GWSC_MAX_SESSIONS_PER_HOST", 5)
MAX_SESSION_PLAYERS = _env_int("GWSC_MAX_SESSION_PLAYERS", 30)
MATCHMAKING_RESULT_LIMIT = _env_int("GWSC_MATCHMAKING_RESULT_LIMIT", 20)
# LSO keeps native matchmaking advertisements alive for eight hours.  The
# private REST session model uses a much shorter heartbeat timeout; native
# sessions need their own lifetime because the game may not heartbeat at the
# same cadence.
NATIVE_MATCHMAKING_SESSION_SECONDS = _env_int(
    "GWSC_NATIVE_MATCHMAKING_SESSION_SECONDS", 8 * 60 * 60
)
# LSO issues one-day P2P certificates from a static local CA.
P2P_CERT_TTL_SECONDS = _env_int("GWSC_P2P_CERT_TTL", 86400)
P2P_CERT_DIR = Path(os.getenv("GWSC_P2P_CERT_DIR", str(DATA_DIR / "certs" / "p2p")))
P2P_CA_KEY_PATH = Path(
    os.getenv("GWSC_P2P_CA_KEY", str(P2P_CERT_DIR / "ca-key.pem"))
)
P2P_CA_CERT_PATH = Path(
    os.getenv("GWSC_P2P_CA_CERT", str(P2P_CERT_DIR / "ca-cert.pem"))
)

# ---------------------------------------------------------------------------
# UDP relay
# ---------------------------------------------------------------------------
RELAY_MAX_DATAGRAM_BYTES = _env_int("GWSC_RELAY_MAX_DATAGRAM", 2048)
RELAY_PEER_TIMEOUT_SECONDS = _env_int("GWSC_RELAY_PEER_TIMEOUT", 60)
RELAY_RATE_PACKETS_PER_SECOND = _env_int("GWSC_RELAY_RATE_PPS", 200)
RELAY_RATE_BURST = _env_int("GWSC_RELAY_RATE_BURST", 400)
RELAY_MAX_PEERS_PER_SESSION = _env_int("GWSC_RELAY_MAX_PEERS_PER_SESSION", 32)

# LSO signs the advertised relay host with a local P-256 key and publishes the
# SPKI public key in GetRelayServers.  These paths are distinct from the TLS
# certificate and the P2P CA; relay keys may be rotated independently.
RELAY_CERT_DIR = Path(
    os.getenv("GWSC_RELAY_CERT_DIR", str(DATA_DIR / "certs" / "relay"))
)
RELAY_SIGNING_KEY_PATH = Path(
    os.getenv("GWSC_RELAY_SIGNING_KEY", str(RELAY_CERT_DIR / "relay_private_key.pem"))
)
RELAY_PUBLIC_KEY_DER_PATH = Path(
    os.getenv("GWSC_RELAY_PUBLIC_KEY_DER", str(RELAY_CERT_DIR / "relay_public_key.der"))
)
RELAY_PUBLIC_KEY_PEM_PATH = Path(
    os.getenv("GWSC_RELAY_PUBLIC_KEY_PEM", str(RELAY_CERT_DIR / "relay_public_key.pem"))
)
RELAY_PUBLIC_KEY_TXT_PATH = Path(
    os.getenv("GWSC_RELAY_PUBLIC_KEY_TXT", str(RELAY_CERT_DIR / "relay_public_key.txt"))
)

# ---------------------------------------------------------------------------
# Economy
# ---------------------------------------------------------------------------
# Shark card claims are disabled by default: a real-money purchase cannot be
# proven from a client request.  Enable only in a private development server.
ALLOW_SHARKCARD_CLAIMS = _env_bool("GWSC_ALLOW_SHARKCARD_CLAIMS", default=False)
STARTER_PACK_AMOUNT = _env_int("GWSC_STARTER_PACK_AMOUNT", 1_000_000)
MAX_ECONOMY_AMOUNT = _env_int("GWSC_MAX_ECONOMY_AMOUNT", 100_000_000)

# ---------------------------------------------------------------------------
# Request limits and abuse controls
# ---------------------------------------------------------------------------
MAX_BODY_BYTES = _env_int("GWSC_MAX_BODY_BYTES", 1_048_576)
LOGIN_RATE_LIMIT = _env_int("GWSC_LOGIN_RATE_LIMIT", 10)
LOGIN_RATE_WINDOW = _env_int("GWSC_LOGIN_RATE_WINDOW", 300)
REGISTER_RATE_LIMIT = _env_int("GWSC_REGISTER_RATE_LIMIT", 5)
REGISTER_RATE_WINDOW = _env_int("GWSC_REGISTER_RATE_WINDOW", 3600)
MESSAGE_RATE_LIMIT = _env_int("GWSC_MESSAGE_RATE_LIMIT", 30)
MESSAGE_RATE_WINDOW = _env_int("GWSC_MESSAGE_RATE_WINDOW", 60)
SESSION_RATE_LIMIT = _env_int("GWSC_SESSION_RATE_LIMIT", 10)
SESSION_RATE_WINDOW = _env_int("GWSC_SESSION_RATE_WINDOW", 60)
GAME_LAUNCH_RATE_LIMIT = _env_int("GWSC_GAME_LAUNCH_RATE_LIMIT", 10)
GAME_LAUNCH_RATE_WINDOW = _env_int("GWSC_GAME_LAUNCH_RATE_WINDOW", 60)
RELAY_REGISTER_RATE_LIMIT = _env_int("GWSC_RELAY_REGISTER_RATE_LIMIT", 20)
RELAY_REGISTER_RATE_WINDOW = _env_int("GWSC_RELAY_REGISTER_RATE_WINDOW", 60)

# ---------------------------------------------------------------------------
# Capture (development only)
# ---------------------------------------------------------------------------
CAPTURE_ENABLED = _env_bool("GWSC_CAPTURE_ENABLED", default=False) and not IS_PRODUCTION
CAPTURE_DIR = Path(os.getenv("GWSC_CAPTURE_DIR", str(BASE_DIR / "captures")))
CAPTURE_MAX_BODY = _env_int("GWSC_CAPTURE_MAX_BODY", 65_536)
CAPTURE_FILE = CAPTURE_DIR / "captures.jsonl"
CAPTURE_HEADER_ALLOWLIST = {
    "accept",
    "accept-encoding",
    "content-type",
    "content-length",
    "user-agent",
    "x-request-id",
    "x-forwarded-for",
    "x-real-ip",
    "x-gwsc-client",
}

# ---------------------------------------------------------------------------
# Web / deployment
# ---------------------------------------------------------------------------
ALLOWED_ORIGINS = _env_list("GWSC_ALLOWED_ORIGINS", "*")
DEBUG_ENDPOINTS = _env_bool("GWSC_DEBUG_ENDPOINTS", default=False) and not IS_PRODUCTION
DOCS_ENABLED = _env_bool("GWSC_DOCS_ENABLED", default=not IS_PRODUCTION)
TRUST_PROXY = _env_bool("GWSC_TRUST_PROXY", default=False)

# Maintenance worker
MAINTENANCE_INTERVAL_SECONDS = _env_int("GWSC_MAINTENANCE_INTERVAL", 30)
