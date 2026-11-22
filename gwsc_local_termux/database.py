"""SQLite persistence, schema definition and forward-only migrations.

The project keeps SQLite for Termux development.  A production deployment
should move to PostgreSQL (see ``docs/setup-vps.md``); the schema and queries
here deliberately avoid SQLite-only features so that migration stays
straightforward.
"""

import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import config
from .time_utils import now_iso

log = logging.getLogger("gwsc.database")

SCHEMA_VERSION = 7

BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rockstar_id INTEGER UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    nickname TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    avatar TEXT DEFAULT 'preset_0',
    crew_tag TEXT DEFAULT '',
    bio TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    is_online INTEGER NOT NULL DEFAULT 0,
    in_gtao INTEGER NOT NULL DEFAULT 0,
    last_seen TEXT,
    last_activity TEXT,
    last_login_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    wallet INTEGER NOT NULL DEFAULT 0,
    bank INTEGER NOT NULL DEFAULT 0,
    rank INTEGER NOT NULL DEFAULT 1,
    starter_pack_owned INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tokens (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_used_at TEXT,
    device TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS friends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    friend_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT,
    UNIQUE(user_id, friend_id)
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'direct',
    name TEXT DEFAULT '',
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    last_read_message_id INTEGER DEFAULT 0,
    joined_at TEXT,
    UNIQUE(conversation_id, user_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS achievements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    achievement_id TEXT NOT NULL,
    unlocked_at TEXT,
    UNIQUE(user_id, achievement_id)
);

CREATE TABLE IF NOT EXISTS game_sessions (
    session_id TEXT PRIMARY KEY,
    host_account_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    host_rockstar_id INTEGER NOT NULL,
    host_ip TEXT NOT NULL,
    host_port INTEGER NOT NULL,
    session_type TEXT DEFAULT 'FreeMode',
    region TEXT DEFAULT 'unknown',
    visibility TEXT DEFAULT 'public',
    state TEXT DEFAULT 'advertised',
    max_players INTEGER DEFAULT 30,
    current_players INTEGER DEFAULT 1,
    is_active INTEGER DEFAULT 1,
    attributes TEXT DEFAULT '{}',
    advertised_data TEXT DEFAULT '',
    available_slots INTEGER DEFAULT 0,
    platform TEXT DEFAULT '',
    game_version TEXT DEFAULT '',
    expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_heartbeat TEXT
);

CREATE TABLE IF NOT EXISTS session_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES game_sessions(session_id) ON DELETE CASCADE,
    account_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    rockstar_id INTEGER NOT NULL,
    peer_address TEXT,
    status TEXT DEFAULT 'joined',
    joined_at TEXT NOT NULL,
    last_seen TEXT,
    UNIQUE(session_id, rockstar_id)
);

CREATE TABLE IF NOT EXISTS presence_records (
    account_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    state TEXT NOT NULL DEFAULT 'offline',
    session_id TEXT,
    last_heartbeat TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    metadata TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS service_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_hash TEXT UNIQUE NOT NULL,
    account_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    service TEXT NOT NULL,
    title TEXT DEFAULT '',
    platform TEXT DEFAULT '',
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    last_used_at TEXT
);

CREATE TABLE IF NOT EXISTS relay_peers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL REFERENCES game_sessions(session_id) ON DELETE CASCADE,
    address TEXT NOT NULL,
    udp_port INTEGER NOT NULL,
    registered_at TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE(address, udp_port)
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    item_id TEXT,
    quantity INTEGER NOT NULL DEFAULT 1,
    currency_delta INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT,
    state TEXT NOT NULL DEFAULT 'completed',
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(account_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS starter_pack_claims (
    account_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    claimed_at TEXT NOT NULL,
    transaction_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS account_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    reason TEXT DEFAULT '',
    changed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""


def get_db() -> sqlite3.Connection:
    """Open a connection with sane, safe defaults."""
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.DatabaseError:
        log.warning("Could not enable SQLite WAL mode", exc_info=True)
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [row[1] for row in rows]


def _ensure_column(
    conn: sqlite3.Connection, table: str, column: str, definition: str
) -> None:
    if column not in _table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        log.info("Migration: added %s.%s", table, column)


def _record_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (version, now_iso()),
    )


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply idempotent migrations to databases created by older builds."""
    # --- Version 2: authentication hardening -----------------------------
    token_columns = _table_columns(conn, "tokens")
    if "token_hash" not in token_columns and "token" in token_columns:
        # The old prototype stored raw tokens.  Rename the column and drop
        # every legacy token: raw tokens are no longer valid credentials.
        conn.execute("ALTER TABLE tokens RENAME COLUMN token TO token_hash")
        conn.execute("DELETE FROM tokens")
        log.info("Migration: invalidated legacy plaintext tokens")
    _ensure_column(conn, "tokens", "last_used_at", "TEXT")
    _ensure_column(conn, "tokens", "device", "TEXT DEFAULT ''")
    _ensure_column(conn, "users", "status", "TEXT NOT NULL DEFAULT 'active'")
    _ensure_column(conn, "users", "last_activity", "TEXT")
    _ensure_column(conn, "users", "last_login_at", "TEXT")
    _ensure_column(conn, "users", "updated_at", "TEXT")
    conn.execute("UPDATE users SET status = 'active' WHERE status IS NULL OR status = ''")
    _record_version(conn, 2)

    # --- Version 3: social model -----------------------------------------
    _ensure_column(conn, "friends", "updated_at", "TEXT")
    conn.execute(
        "UPDATE friends SET status = 'pending' WHERE status = 'pending_outgoing'"
    )
    conn.execute("UPDATE friends SET updated_at = COALESCE(updated_at, created_at)")
    _ensure_column(conn, "conversations", "created_by", "INTEGER")
    _ensure_column(conn, "conversation_members", "joined_at", "TEXT")
    _record_version(conn, 3)

    # --- Version 4: native services and matchmaking ----------------------
    _ensure_column(conn, "game_sessions", "host_account_id", "INTEGER")
    _ensure_column(conn, "game_sessions", "region", "TEXT DEFAULT 'unknown'")
    _ensure_column(conn, "game_sessions", "visibility", "TEXT DEFAULT 'public'")
    _ensure_column(conn, "game_sessions", "state", "TEXT DEFAULT 'advertised'")
    _ensure_column(conn, "game_sessions", "last_heartbeat", "TEXT")
    _ensure_column(conn, "session_members", "account_id", "INTEGER")
    _ensure_column(conn, "session_members", "status", "TEXT DEFAULT 'joined'")
    _ensure_column(conn, "session_members", "last_seen", "TEXT")
    conn.execute(
        "UPDATE game_sessions SET state = CASE WHEN is_active = 1 THEN 'advertised' ELSE 'closed' END "
        "WHERE state IS NULL OR state = ''"
    )
    _record_version(conn, 4)

    # --- Version 5: economy ----------------------------------------------
    conn.execute(
        """
        INSERT OR IGNORE INTO starter_pack_claims (account_id, claimed_at, transaction_id)
        SELECT id, COALESCE(updated_at, created_at, ?), NULL
        FROM users WHERE starter_pack_owned = 1
        """,
        (now_iso(),),
    )
    _record_version(conn, 5)

    # --- Version 6: achievements -----------------------------------------
    # Older builds created this table outside the canonical schema, and a
    # fresh install was missing it entirely.  BASE_SCHEMA creates it with
    # IF NOT EXISTS; record the version for completeness.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            achievement_id TEXT NOT NULL,
            unlocked_at TEXT,
            UNIQUE(user_id, achievement_id)
        )
        """
    )
    _record_version(conn, 6)

    # --- Version 7: native matchmaking ------------------------------------
    # LSO stores the opaque Data value supplied by the advertising client and
    # returns it verbatim from Find.  The server must not synthesise it.
    _ensure_column(conn, "game_sessions", "advertised_data", "TEXT DEFAULT ''")
    _ensure_column(conn, "game_sessions", "available_slots", "INTEGER DEFAULT 0")
    _ensure_column(conn, "game_sessions", "platform", "TEXT DEFAULT ''")
    _ensure_column(conn, "game_sessions", "game_version", "TEXT DEFAULT ''")
    _ensure_column(conn, "game_sessions", "expires_at", "TEXT")
    _record_version(conn, 7)

    # Indexes are created by BASE_SCHEMA, but older databases need them too.
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
        CREATE INDEX IF NOT EXISTS idx_users_nickname ON users(nickname);
        CREATE INDEX IF NOT EXISTS idx_users_online ON users(is_online, last_activity);
        CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(user_id);
        CREATE INDEX IF NOT EXISTS idx_tokens_expiry ON tokens(expires_at);
        CREATE INDEX IF NOT EXISTS idx_friends_user ON friends(user_id, status);
        CREATE INDEX IF NOT EXISTS idx_friends_friend ON friends(friend_id, status);
        CREATE INDEX IF NOT EXISTS idx_conv_members_user ON conversation_members(user_id);
        CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
        CREATE INDEX IF NOT EXISTS idx_achievements_user ON achievements(user_id, unlocked_at);
        CREATE INDEX IF NOT EXISTS idx_sessions_active ON game_sessions(is_active, state, updated_at);
        CREATE INDEX IF NOT EXISTS idx_sessions_host ON game_sessions(host_rockstar_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_native ON game_sessions(platform, expires_at, is_active);
        CREATE INDEX IF NOT EXISTS idx_session_members_account ON session_members(account_id);
        CREATE INDEX IF NOT EXISTS idx_presence_expiry ON presence_records(expires_at);
        CREATE INDEX IF NOT EXISTS idx_tickets_account ON service_tickets(account_id, service);
        CREATE INDEX IF NOT EXISTS idx_tickets_expiry ON service_tickets(expires_at);
        CREATE INDEX IF NOT EXISTS idx_relay_session ON relay_peers(session_id);
        CREATE INDEX IF NOT EXISTS idx_transactions_account ON transactions(account_id, created_at);
        """
    )


def init_db() -> None:
    conn = get_db()
    try:
        with conn:
            conn.executescript(BASE_SCHEMA)
            _migrate(conn)
        try:
            config.DB_PATH.chmod(0o600)
        except OSError:
            pass
    finally:
        conn.close()
    log.info("Database initialized at %s (schema v%d)", config.DB_PATH, SCHEMA_VERSION)


def get_next_rockstar_id(conn: sqlite3.Connection) -> int:
    """Allocate the next public Rockstar-style account number.

    Callers must run this inside the same transaction that inserts the user
    so a unique-constraint failure rolls the allocation back.
    """
    row = conn.execute("SELECT MAX(rockstar_id) FROM users").fetchone()
    if row and row[0] is not None:
        return int(row[0]) + 1
    return 10001


def reset_database_for_tests() -> None:
    """Drop the test database file.  Only used by the automated tests."""
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(config.DB_PATH) + suffix)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    init_db()


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(row) for row in rows]
