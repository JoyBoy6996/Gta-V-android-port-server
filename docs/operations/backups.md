# Backups and Restore

The database is a single SQLite file with WAL enabled. Do **not** copy the
live `.db` file while the server is writing — use the built-in online backup
helper, which uses SQLite's backup API.

## Create a backup

From the repository root, using the same interpreter/venv as the server:

```bash
gwsc_local_termux/.venv/bin/python -m gwsc_local_termux.backup
```

- Output: `gwsc_local_termux/backups/gwsc_backup_YYYYMMDD_HHMMSS.db`
- Mode `0600`.
- Runs `PRAGMA integrity_check`; a corrupt backup is reported and not kept.
- Safe while the server is running.

You can also call `backup_database(Path(...))` from Python.

## Restore

**Stop the server first.** Restoring under a running process can corrupt the
database or leave stale WAL files.

```bash
./gwsc_local_termux/stop.sh
gwsc_local_termux/.venv/bin/python -m gwsc_local_termux.backup restore \
  gwsc_local_termux/backups/gwsc_backup_YYYYMMDD_HHMMSS.db
./gwsc_local_termux/run.sh
```

The restore helper:

1. Runs `PRAGMA integrity_check` on the backup and refuses corrupt files.
2. Takes a `pre_restore_*.db` safety backup of the current database.
3. Copies the backup into place using the SQLite backup API.

## Schedule

Termux (cron via `cronie`, or `termux-job-scheduler`):

```bash
0 3 * * * cd /data/user/0/com.termux/files/home/gtaserver && gwsc_local_termux/.venv/bin/python -m gwsc_local_termux.backup
```

systemd timer (VPS): create a `gwsc-backup.service` calling the same command
as the `gwsc` user, and a `gwsc-backup.timer` with `OnCalendar=*-*-* 03:00:00`.

## Retention

Backups contain password hashes and account data. There is no built-in
retention policy. Implement one appropriate to your storage:

- keep a short recent window (e.g. 7 daily + 4 weekly);
- encrypt off-site copies;
- restrict permissions to the service user;
- never commit backups (the directory is gitignored).

## Verify a backup

```bash
sqlite3 gwsc_local_termux/backups/gwsc_backup_YYYYMMDD_HHMMSS.db \
  'PRAGMA integrity_check; SELECT COUNT(*) FROM users;'
```

A healthy backup prints `ok` and a user count.

## What is not backed up

- TLS certificates/keys (`cert.pem`, `key.pem`) — regenerate if lost.
- `.env` / secrets — store separately in a secret manager.
- Logs and captures — operational artifacts, not state.
- The in-memory relay registry and SSE connections — they rebuild at runtime.
