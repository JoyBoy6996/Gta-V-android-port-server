import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

SERVER_NAME = os.getenv("GWSC_SERVER_NAME", "Unknown Beaches Social Club")
SERVER_VERSION = os.getenv("GWSC_SERVER_VERSION", "1.0.0")
SERVER_HOST = os.getenv("GWSC_HOST", "0.0.0.0")

# Auto-detect Render environment and port
PORT_ENV = os.getenv("PORT")
IS_RENDER = bool(os.getenv("RENDER") or PORT_ENV)
SERVER_PORT = int(PORT_ENV or os.getenv("GWSC_PORT", "8443"))

RELAY_HOST = os.getenv("GWSC_RELAY_HOST", "0.0.0.0")
RELAY_PORT = int(os.getenv("GWSC_RELAY_PORT", "6672"))

# On Render or cloud platforms with edge TLS, disable internal SSL by default
SSL_ENABLED = os.getenv("GWSC_SSL_ENABLED", "false" if IS_RENDER else "true").lower() in ("1", "true", "yes")
SSL_CERT = BASE_DIR / os.getenv("GWSC_SSL_CERT", "cert.pem")
SSL_KEY = BASE_DIR / os.getenv("GWSC_SSL_KEY", "key.pem")

# Persistent storage directory
DATA_DIR_PATH = os.getenv("DATA_DIR")
if DATA_DIR_PATH:
    DATA_DIR = Path(DATA_DIR_PATH)
else:
    DATA_DIR = BASE_DIR / "data"

DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / os.getenv("GWSC_DB_NAME", "gwsc.db")

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "gwsc.log"

BACKUP_DIR = BASE_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

REGISTRATION_ENABLED = os.getenv("GWSC_REGISTRATION", "true").lower() in ("1", "true", "yes")
TOKEN_EXPIRY_SECONDS = int(os.getenv("GWSC_TOKEN_EXPIRY", "86400"))
