import sqlite3
import datetime
import logging
from typing import Optional, Dict, Any, List
from .config import DB_PATH

log = logging.getLogger("gwsc.database")

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_db()
    with conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rockstar_id INTEGER UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            nickname TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            avatar TEXT DEFAULT 'preset_0',
            crew_tag TEXT DEFAULT '',
            bio TEXT DEFAULT 'Ready to play GTA Online!',
            is_online BOOLEAN DEFAULT 0,
            in_gtao BOOLEAN DEFAULT 0,
            last_seen TEXT,
            created_at TEXT,
            wallet INTEGER DEFAULT 5000000,
            bank INTEGER DEFAULT 25000000,
            rank INTEGER DEFAULT 100,
            starter_pack_owned BOOLEAN DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS tokens (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TEXT,
            expires_at TEXT
        );

        CREATE TABLE IF NOT EXISTS friends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            friend_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            status TEXT NOT NULL,
            created_at TEXT,
            UNIQUE(user_id, friend_id)
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL DEFAULT 'direct',
            name TEXT DEFAULT '',
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS conversation_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            last_read_message_id INTEGER DEFAULT 0,
            UNIQUE(conversation_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            body TEXT NOT NULL,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS game_sessions (
            session_id TEXT PRIMARY KEY,
            host_rockstar_id INTEGER NOT NULL,
            host_ip TEXT NOT NULL,
            host_port INTEGER NOT NULL,
            session_type TEXT DEFAULT 'FreeMode',
            max_players INTEGER DEFAULT 30,
            current_players INTEGER DEFAULT 1,
            is_active BOOLEAN DEFAULT 1,
            attributes TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS session_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES game_sessions(session_id) ON DELETE CASCADE,
            rockstar_id INTEGER NOT NULL,
            peer_address TEXT,
            joined_at TEXT,
            UNIQUE(session_id, rockstar_id)
        );

        CREATE TABLE IF NOT EXISTS achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            achievement_id TEXT NOT NULL,
            unlocked_at TEXT,
            UNIQUE(user_id, achievement_id)
        );
        """)
    conn.close()
    log.info("Database initialized at %s", DB_PATH)

def get_next_rockstar_id(conn) -> int:
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(rockstar_id) FROM users")
    row = cursor.fetchone()
    if row and row[0] is not None:
        return row[0] + 1
    return 10001
