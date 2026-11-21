"""Consistent SQLite backup and restore helpers.

Copying the live ``.db`` file is not safe when WAL mode is enabled, so the
backup uses SQLite's online backup API.  Stop the server before restoring.
"""

import datetime
import logging
import sqlite3
from pathlib import Path
from typing import Optional

from . import config

log = logging.getLogger("gwsc.backup")


def backup_database(destination: Optional[Path] = None) -> Optional[Path]:
    if not config.DB_PATH.exists():
        log.warning("Database %s does not exist; nothing to back up", config.DB_PATH)
        return None

    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = destination or config.BACKUP_DIR / f"gwsc_backup_{timestamp}.db"

    source = sqlite3.connect(str(config.DB_PATH))
    target = sqlite3.connect(str(backup_file))
    try:
        with target:
            source.backup(target)
    finally:
        source.close()
        target.close()

    try:
        backup_file.chmod(0o600)
    except OSError:
        pass

    check = sqlite3.connect(str(backup_file))
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if result != "ok":
        log.error("Backup integrity check failed: %s", result)
        return None

    log.info("Database backed up to %s", backup_file)
    return backup_file


def restore_database(backup_path: Path) -> bool:
    if not backup_path.exists():
        log.error("Backup file %s does not exist", backup_path)
        return False

    check = sqlite3.connect(str(backup_path))
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if result != "ok":
        log.error("Refusing to restore a corrupt backup: %s", result)
        return False

    # Keep the current database as a safety net before overwriting it.
    if config.DB_PATH.exists():
        backup_database(
            config.BACKUP_DIR
            / f"pre_restore_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        )

    source = sqlite3.connect(str(backup_path))
    target = sqlite3.connect(str(config.DB_PATH))
    try:
        with target:
            source.backup(target)
    finally:
        source.close()
        target.close()
    log.info("Database restored from %s", backup_path)
    return True


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) > 1 and sys.argv[1] == "restore":
        if len(sys.argv) > 2:
            restore_database(Path(sys.argv[2]))
        else:
            print("Usage: python -m gwsc_local_termux.backup restore <path_to_backup>")
    else:
        backup_database()
