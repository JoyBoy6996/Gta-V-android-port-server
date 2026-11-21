# Monitoring and Health

## Health endpoint

```bash
curl -s http://127.0.0.1:8443/health
```

```json
{
  "status": "ok",
  "database": true,
  "relay": {
    "registry": {
      "peers": 0,
      "sessions": 0,
      "per_session": {}
    },
    "packets": {
      "received": 0,
      "forwarded": 0,
      "dropped_unregistered": 0,
      "dropped_size": 0,
      "dropped_rate": 0,
      "dropped_send": 0
    }
  },
  "sseConnections": 0,
  "environment": "production"
}
```

- `status` is `degraded` when the database probe fails.
- `database` runs a real `SELECT 1`.
- `relay.registry` is the in-memory peer/session registry; `relay.packets`
  holds the relay counters.
- `sseConnections` is the number of active event-stream connections.

Use `/health` for container healthchecks and uptime monitors. It is
unauthenticated and returns no account data.

## Root endpoint

`GET /` returns name, version, status and a "private server" note. Useful as
a simple liveness check.

## Info endpoint

`GET /gwsc/api/info` returns `accounts` and a **real** `online` count derived
from unexpired presence records. A zero count is valid. Do not treat it as a
fabricated metric.

## Logs

- Location: `GWSC_LOG_DIR/gwsc.log` (default `gwsc_local_termux/logs`).
- Also streamed to stdout.
- A redaction filter removes passwords, bearer tokens, cookies, tickets and
  Social Club tokens from log records and exception text.
- Access log lines include method, path, status, duration and request ID.

Tail logs:

```bash
tail -f gwsc_local_termux/logs/gwsc.log
journalctl -u gwsc -f          # systemd
docker compose logs -f gwsc    # Docker
```

## Request IDs

Every request gets an `X-Request-ID` (client-supplied values are sanitised).
The same ID appears in the access log and in 500 responses as `requestId`.
Use it to correlate a client report with a log line.

## Relay counters

`GET /gwsc/api/relay/status` (authenticated) shows the caller's peers plus
aggregate registry stats. `/health` exposes the aggregate counters. Watch:

- `dropped_unregistered` — clients sending before registering;
- `dropped_size` — datagrams above `GWSC_RELAY_MAX_DATAGRAM`;
- `dropped_rate` — token-bucket throttling;
- `dropped_send` — OS/send errors (often a dead peer).

## Database

- WAL mode; expect `gwsc.db-wal` and `gwsc.db-shm` beside the database.
- The maintenance worker expires presence, sessions, tokens, tickets and
  relay peers every `GWSC_MAINTENANCE_INTERVAL` seconds.
- Watch database size and backup success. See
  [`backups.md`](backups.md).

## Alerts worth setting

- `/health` non-200 or `status != "ok"` for > 2 minutes.
- Database file growing unexpectedly (runaway messages/sessions).
- Sustained `dropped_rate` / `dropped_unregistered` growth (abuse or
  misconfiguration).
- Repeated 401/403/429 spikes in the access log (credential stuffing or a
  broken client).
- Backup job failure or missing recent backup.

## What is not instrumented

There is no Prometheus endpoint, tracing or per-endpoint latency histogram.
Metrics are limited to `/health` counters and the log. Add a metrics library
if you need time-series data.
