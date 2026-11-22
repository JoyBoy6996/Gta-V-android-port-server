# GWSC Local Backend (Termux package)

This is the application package for the private GTA V Android backend. See
the [repository README](../README.md) for the honest status table and the
full documentation index.

> Private experiment for a modified client. Not affiliated with Rockstar
> Games. Client compatibility is **unverified**.

## Install and run

```bash
cd gwsc_local_termux
./install.sh          # add --dev to install test dependencies
./run.sh
curl -k https://127.0.0.1:8443/health
```

Stop/restart with `./stop.sh` / `./restart.sh`.

## What is implemented

- **GWSC REST/SSE** (`/gwsc/api/*`, also unprefixed):
  - `register`, `login`, `logout`, `logout-all`
  - `me`, nickname, password, avatar presets
  - public profiles, block/unblock
  - friends and requests
  - direct/group conversations, messages, read state
  - `game-launch` ticket for the installed launcher
  - starter pack; shark-card claims disabled by default
  - per-account SSE events
- **Native ROS-style**: `.asmx` `CreateTicket*` / `CreateScAuthToken` methods,
  `CreateTicket`, `ros/ticket/renew`, `ros/ticket/invalidate`,
  `RosGamersSettings.json`, `CommitPresence`, `Presence`, matchmaking
  advertise/find/join/unadvertise. Issued credentials omit privilege 7 (the
  client's banned flag) and include the parser-required XML fields.
- **UDP relay**: authenticated registration, session isolation, rate limits.
- **Operations**: SQLite migrations, online backup, health endpoint.

## What is not verified

- Login/profile response shapes accepted by the Android client.
- The exact native call order after launch, XML namespaces and any intermediate
  token exchange. (The `.asmx` method names and `CreateTicket` field names are
  `SOURCE_CONFIRMED` from static inspection; the full sequence is not.)
- Matchmaking contract and real session discovery.
- Gameplay transport over the relay.
- Any end-to-end game login or session.

Only a login **request** and the launcher launch exchange have been
observed/source-confirmed. Do not treat this package as a drop-in GTA Online
server.

## Layout

| Path | Purpose |
| --- | --- |
| `server.py` | App assembly, middleware, lifespan. |
| `config.py` | Environment configuration. |
| `database.py` | Schema + forward migrations. |
| `auth.py`, `security.py` | Tokens, passwords, rate limiting. |
| `routes/` | Endpoint handlers. |
| `multiplayer_relay.py` | UDP relay. |
| `maintenance.py` | Periodic cleanup. |
| `capture.py` | Development capture + redaction. |
| `backup.py` | Backup/restore. |
| `generate_certs.py` | Self-signed TLS with SANs. |
| `data/`, `logs/`, `backups/` | Runtime state (gitignored). |

## Backup and restore

```bash
.venv/bin/python -m gwsc_local_termux.backup
.venv/bin/python -m gwsc_local_termux.backup restore backups/gwsc_backup_YYYYMMDD_HHMMSS.db
```

Stop the server before restoring. See
[`../docs/operations/backups.md`](../docs/operations/backups.md).

## Tests

```bash
./install.sh --dev
GWSC_SSL_ENABLED=false GWSC_RELAY_PORT=0 .venv/bin/python -m pytest -q
```

## More documentation

- [Architecture](../docs/architecture.md)
- [Environment variables](../docs/environment-variables.md)
- [Protocol inventory](../docs/protocol/ANDROID_PROTOCOL_INVENTORY.md)
- [Compatibility matrix](../docs/testing/compatibility-matrix.md)
- [Troubleshooting](../docs/operations/troubleshooting.md)
