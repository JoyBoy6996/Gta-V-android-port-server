# Troubleshooting

Start with the log and `/health`:

```bash
tail -n 100 gwsc_local_termux/logs/gwsc.log
curl -s http://127.0.0.1:8443/health
```

## Server will not start

| Symptom | Cause / fix |
| --- | --- |
| `ModuleNotFoundError: fastapi` | Dependencies not installed. Run `./install.sh` or use `.venv/bin/python`. |
| `Address already in use` | Another instance is running. `./stop.sh`, or set `GWSC_PORT`/`GWSC_RELAY_PORT`. |
| `Permission denied` on `key.pem` | Fix ownership/mode: `chmod 600 cert.pem key.pem`. |
| Database locked | Another process holds a write lock. Stop duplicate servers; WAL allows only one writer. |
| `setsid`/`nohup` missing | `run.sh` falls back; install `util-linux`/`coreutils` if needed. |

## TLS / certificate problems

- **Self-signed warning** — expected in Termux. The client may need
  `SELF_SIGNED_CERT=1` and a rebuilt APK.
- **Hostname mismatch** — regenerate certificates after the LAN IP changes:
  `gwsc_local_termux/.venv/bin/python -m gwsc_local_termux.generate_certs`.
- **Behind a proxy** — set `GWSC_SSL_ENABLED=false` and let the proxy
  terminate TLS. Do not enable both.
- **`curl` works with `-k` only** — the certificate is not trusted; that is
  normal for the generated cert.

## Login fails

- **401 on every login** — check the email/nickname and password. Errors are
  intentionally generic. Verify the account exists:
  `sqlite3 gwsc_local_termux/data/gwsc.db 'SELECT nickname,status FROM users;'`
- **403 `account_disabled`** — the account `status` is not `active`.
- **429** — rate limited. Wait for `Retry-After`, or raise
  `GWSC_LOGIN_RATE_LIMIT`.
- **All old tokens stopped working after upgrade** — expected: the migration
  invalidates legacy plaintext tokens. Log in again.
- **Tokens invalid after changing `GWSC_TOKEN_PEPPER`** — expected. Keep the
  pepper stable or re-issue tokens.

## Game client connects but does nothing

This is the current known unknown. Only a login request has been observed.
The response contract is `UNVERIFIED`. Next steps:

1. Enable capture: `GWSC_CAPTURE_ENABLED=true` (development only).
2. Reproduce one action at a time.
3. Inspect `gwsc_local_termux/captures/captures.jsonl` (redacted).
4. Compare against [`protocol/ANDROID_PROTOCOL_INVENTORY.md`](../protocol/ANDROID_PROTOCOL_INVENTORY.md).

Do not "fix" it by inventing fields or returning generic success — that hides
the real mismatch.

## Native / ROS requests return 404

Unknown native paths intentionally return 404. The old catch-all success
handler was removed. If the client calls a path not implemented here, add a
real handler with validation and a test; do not restore a catch-all.

## Native requests return 401

Native handlers require a GWSC token from one of: form field
`scAuthToken`/`authToken`/`token`/`ticket`/`sessionTicket`, query parameter,
`Authorization: Bearer`, `X-GWSC-Token`, or `ros-SessionTicket` (and spelling
variants). There is no fallback account. Check that the client sends a valid,
unexpired ticket.

## SSE stream disconnects

- Proxies must disable buffering for `/gwsc/api/events` (see
  `deploy/nginx.conf.example`).
- The server sends a heartbeat comment every `GWSC_SSE_HEARTBEAT` seconds.
- Per-account connection cap: `GWSC_SSE_MAX_CONNECTIONS` (429 when exceeded).
- Event streams require a valid token (header or `?token=`).

## Relay packets are dropped

| Counter | Meaning |
| --- | --- |
| `dropped_unregistered` | Source address never called `POST /gwsc/api/relay/register`. |
| `dropped_size` | Datagram > `GWSC_RELAY_MAX_DATAGRAM`. |
| `dropped_rate` | Exceeded `GWSC_RELAY_RATE_PPS`/burst. |
| `dropped_send` | Send error; peer likely gone. |

Check `GET /gwsc/api/relay/status` and confirm the session is active and the
account is a host/member. The relay only forwards within one `session_id`.

## Body rejected with 413

The request exceeds `GWSC_MAX_BODY_BYTES` (1 MiB default). Raise it only if
the client legitimately sends larger payloads.

## Validation errors (422)

The response has `error: "invalid_request"` and a `detail` list. Common
causes: missing field, wrong type, nickname rules, password length, avatar
preset not in the list.

## Database migration issues

Migrations are forward-only and idempotent. If a legacy database fails:

1. Stop the server.
2. Back it up: `python -m gwsc_local_termux.backup`.
3. Inspect `schema_migrations` and the error in the log.
4. Restore a known-good backup if needed.

Never delete a production database without a verified backup.

## Captures are not written

- Capture is disabled in production and when `GWSC_CAPTURE_ENABLED=false`.
- The directory must be writable (`captures/`, mode 0700).
- Bodies above `GWSC_CAPTURE_MAX_BODY` are truncated, not skipped.

## Game launches but Online shows login

This is a **native sign-in** question, not a launcher-popup question. The
launcher sets `GTAV_LAUNCH_TOKEN`, but static analysis has not proven that the
native game consumes it for ROS authentication. Do not rebuild the APK until a
native request shows otherwise.

Capture one attempt with:

```bash
tail -f gwsc_local_termux/logs/gwsc.log | grep -E \
  'auth\.asmx|matchmaking\.asmx|GeoLocation\.asmx|Presence\.asmx|game-launch'
```

Redact every token, ticket, cookie and email before sharing. The useful
evidence is the **path**, HTTP status and request ID, not the credential
values. If no native `.asmx` paths appear after tapping Online, the client is
not reaching this backend and the host/`-rosHost=` handoff is still the
problem. If they appear with 401, the credential field/spelling is the problem.
See [`protocol/ps4-server-comparison.md`](../protocol/ps4-server-comparison.md).

## Still stuck

Collect: the failing request (redacted), the `X-Request-ID`, the matching log
lines, `/health`, and the relevant protocol doc. Open an issue with those,
without real credentials or tokens.
