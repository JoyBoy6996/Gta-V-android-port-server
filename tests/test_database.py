import sqlite3
from contextlib import closing

from gwsc_local_termux import config, database


def _columns(conn, table):
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def test_fresh_database_has_current_schema(client):
    with closing(database.get_db()) as conn:
        assert "token_hash" in _columns(conn, "tokens")
        assert "status" in _columns(conn, "users")
        assert "last_activity" in _columns(conn, "users")
        assert "created_by" in _columns(conn, "conversations")
        assert "state" in _columns(conn, "game_sessions")
        assert "achievement_id" in _columns(conn, "achievements")
        assert "primary_clan_id" in _columns(conn, "users")
        assert "sort_order" in _columns(conn, "presence_attributes")
        for table in (
            "presence_attributes",
            "social_messages",
            "clans",
            "clan_memberships",
            "ugc_contents",
            "news",
            "stat_lookups",
        ):
            assert conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()[0] == 1
        versions = {
            row[0]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        assert {2, 3, 4, 5, 6, 7, 8}.issubset(versions)


def test_legacy_database_is_migrated(tmp_path):
    legacy_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy_path)
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rockstar_id INTEGER UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            nickname TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            avatar TEXT DEFAULT 'preset_0',
            crew_tag TEXT DEFAULT '',
            bio TEXT DEFAULT '',
            is_online BOOLEAN DEFAULT 0,
            in_gtao BOOLEAN DEFAULT 0,
            last_seen TEXT,
            created_at TEXT,
            wallet INTEGER DEFAULT 5000000,
            bank INTEGER DEFAULT 25000000,
            rank INTEGER DEFAULT 100,
            starter_pack_owned BOOLEAN DEFAULT 0
        );
        CREATE TABLE tokens (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT,
            expires_at TEXT
        );
        CREATE TABLE friends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            friend_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT,
            UNIQUE(user_id, friend_id)
        );
        INSERT INTO users (rockstar_id, email, nickname, password_hash, created_at)
        VALUES (10001, 'legacy@example.invalid', 'legacy', 'hash', '2020-01-01T00:00:00+00:00');
        INSERT INTO tokens (token, user_id, created_at, expires_at)
        VALUES ('raw-legacy-token', 1, '2020-01-01T00:00:00+00:00', '2099-01-01T00:00:00+00:00');
        INSERT INTO friends (user_id, friend_id, status, created_at)
        VALUES (1, 1, 'pending_outgoing', '2020-01-01T00:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()

    config.DB_PATH = legacy_path
    database.init_db()

    with closing(database.get_db()) as migrated:
        token_columns = _columns(migrated, "tokens")
        assert "token_hash" in token_columns
        assert "token" not in token_columns
        # Legacy plaintext tokens are deliberately invalidated.
        assert migrated.execute("SELECT COUNT(*) FROM tokens").fetchone()[0] == 0

        user_columns = _columns(migrated, "users")
        assert "status" in user_columns
        assert "last_activity" in user_columns
        assert migrated.execute("SELECT status FROM users WHERE id = 1").fetchone()[0] == "active"

        friend = migrated.execute("SELECT status, updated_at FROM friends WHERE id = 1").fetchone()
        assert friend["status"] == "pending"
        assert friend["updated_at"] is not None

        # A legacy database without an achievements table must gain one.
        assert "achievement_id" in _columns(migrated, "achievements")

        # Schema v8 adds the LSO parity social/content tables.
        assert "value_type" in _columns(migrated, "presence_attributes")
        assert "sort_order" in _columns(migrated, "presence_attributes")
        assert "primary_clan_id" in _columns(migrated, "users")
        assert "delivered_at" in _columns(migrated, "social_messages")
        assert "rank_id" in _columns(migrated, "clan_memberships")
        assert "is_published" in _columns(migrated, "ugc_contents")
        versions = {
            row[0]
            for row in migrated.execute("SELECT version FROM schema_migrations").fetchall()
        }
        assert 8 in versions
