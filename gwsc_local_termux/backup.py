import shutil
import datetime
import logging
from pathlib import Path
from .config import DB_PATH, BACKUP_DIR

log = logging.getLogger("gwsc.backup")

def backup_database() -> Optional[Path]:
    if not DB_PATH.exists():
        log.warning("Database file %s does not exist yet. Nothing to backup.", DB_PATH)
        return None
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = BACKUP_DIR / f"gwsc_backup_{timestamp}.db"
    shutil.copy2(DB_PATH, backup_file)
    log.info("Database backed up successfully to %s", backup_file)
    return backup_file

def restore_database(backup_path: Path) -> bool:
    if not backup_path.exists():
        log.error("Backup file %s does not exist", backup_path)
        return False
    shutil.copy2(backup_path, DB_PATH)
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
