# GTA V Android Private Backend (GWSC)

A private, self-hosted backend for a modified GTA V Android client, built
with Python and FastAPI. It runs on Android/Termux, in Docker, or on a VPS.

> **Not affiliated with Rockstar Games.** This is a private-server experiment
> for a modified client. It does not claim Grand Theft Auto Online
> compatibility, and it is not a replacement for Rockstar's services.

> **Compatibility is unverified.** The only captured client traffic is a
> login request. Response shapes, native ROS tasks, matchmaking and gameplay
> transport are `UNVERIFIED`. Do not read this repository as a working
> "GTA Online server". See
> [`docs/testing/compatibility-matrix.md`](docs/testing/compatibility-matrix.md).

## Status at a glance

| Area | Status | Evidence |
| --- | --- | --- |
| Account registration, login, logout | Implemented + tested | Login request `OBSERVED`; responses `UNVERIFIED` |
| Token auth (expiry, revocation, status) | Implemented + tested | `TESTED` |
| Password hashing + legacy upgrade | Implemented + tested | `TESTED` |
| Profiles, avatars, blocking | Implemented + tested (partly) | `UNVERIFIED` contracts |
| Friends | Implemented + tested | `UNVERIFIED` contracts |
| Conversations + messages | Implemented + tested | `UNVERIFIED` contracts |
| SSE events (per account) | Implemented + tested | SSE suspected `SOURCE_CONFIRMED` |
| Economy (starter pack, shark cards) | Implemented; shark cards off by default | `UNVERIFIED` |
| Native tickets (`CreateTicket`, renew, invalidate) | Implemented + tested | `UNVERIFIED` XML |
| Presence | Implemented + tested | `UNVERIFIED` |
| Matchmaking advertise/find/join | Implemented + tested | `INFERRED` / `UNVERIFIED` |
| UDP relay (registration + forwarding) | Implemented + tested in isolation | `INFERRED` / `UNVERIFIED` |
| Two-device gameplay over the relay | **Not verified** | Unknown |
| Android client end-to-end login/session | **Not verified** | Unknown |

The test suite proves internal behaviour (auth, validation, authorization,
isolation, limits), not client compatibility. Details:
[`docs/testing/compatibility-matrix.md`](docs/testing/compatibility-matrix.md).

## Features

- **GWSC REST/SSE API** under `/gwsc/api/*` (also mounted unprefixed for the
  original prototype):
  - auth: `register`, `login`, `logout`, `logout-all`
  - profile: `me`, nickname, password, avatar presets
  - users: public profile, block/unblock
  - social: friends, requests, accept/reject/remove
  - chat: direct/group conversations, messages, read state, SSE events
  - economy: starter pack, optional shark-card claims (development only)
- **Native ROS-style services**: `CreateTicket`, ticket renew/invalidate,
  `RosGamersSettings.json`, presence, matchmaking.
- **Session-aware UDP relay** on `6672/udp` with authenticated registration,
  per-peer rate limiting and session isolation.
- **Security**: PBKDF2 password hashing, hashed bearer tokens and tickets,
  per-route validation and authorization, rate limiting, body limits,
  log redaction, development-only capture.
- **Operations**: SQLite forward migrations, online backups, health endpoint,
  Docker/systemd/nginx deployment files.

## Quick start (Termux)

```bash
pkg install python openssl git
cd gwsc_local_termux
./install.sh
./run.sh
curl -k https://127.0.0.1:8443/health
```

Stop/restart with `./stop.sh` / `./restart.sh`. Full guide:
[`docs/setup-termux.md`](docs/setup-termux.md).

## Quick start (Docker)

```bash
docker compose up -d --build
curl http://127.0.0.1:8443/health
```

Put a TLS-terminating proxy in front of `8443`. Full guide:
[`docs/setup-vps.md`](docs/setup-vps.md).

## Tests

```bash
./gwsc_local_termux/install.sh --dev
GWSC_SSL_ENABLED=false GWSC_RELAY_PORT=0 \
  gwsc_local_termux/.venv/bin/python -m pytest -q
```

The suite creates isolated temporary databases; it never touches real data.
Test fixtures and safe account practices:
[`docs/testing/test-accounts.md`](docs/testing/test-accounts.md).

## Configuration

Every setting is an environment variable. Copy `.env.example` to `.env`
(never commit it). Full reference:
[`docs/environment-variables.md`](docs/environment-variables.md).

Notable defaults:

- HTTPS on `8443`, UDP relay on `6672`.
- Registration enabled; 24-hour tokens.
- Shark-card claims **disabled**.
- Capture and API docs **disabled in production**.

## Documentation

| Document | Contents |
| --- | --- |
| [`docs/architecture.md`](docs/architecture.md) | Modules, request pipeline, data model, limitations. |
| [`docs/setup-termux.md`](docs/setup-termux.md) | Android/Termux setup. |
| [`docs/setup-vps.md`](docs/setup-vps.md) | Docker, systemd, nginx, firewall. |
| [`docs/environment-variables.md`](docs/environment-variables.md) | Every setting and default. |
| [`docs/operations/backups.md`](docs/operations/backups.md) | Backup/restore. |
| [`docs/operations/monitoring.md`](docs/operations/monitoring.md) | Health, logs, metrics. |
| [`docs/operations/troubleshooting.md`](docs/operations/troubleshooting.md) | Common failures. |
| [`docs/protocol/ANDROID_PROTOCOL_INVENTORY.md`](docs/protocol/ANDROID_PROTOCOL_INVENTORY.md) | Route inventory + evidence labels. |
| [`docs/protocol/gwsc.md`](docs/protocol/gwsc.md) | GWSC REST/SSE contract. |
| [`docs/protocol/native-ros.md`](docs/protocol/native-ros.md) | Tickets, presence, settings. |
| [`docs/protocol/matchmaking.md`](docs/protocol/matchmaking.md) | Session lifecycle. |
| [`docs/protocol/multiplayer-transport.md`](docs/protocol/multiplayer-transport.md) | UDP relay. |
| [`docs/testing/compatibility-matrix.md`](docs/testing/compatibility-matrix.md) | What is tested vs unverified. |
| [`docs/testing/test-accounts.md`](docs/testing/test-accounts.md) | Safe test accounts. |

## Evidence labels

Protocol claims use consistent labels:

- `OBSERVED` — captured from the game or verified in client code.
- `SOURCE_CONFIRMED` — found in static inspection, not yet in traffic.
- `INFERRED` — reasoned hypothesis.
- `UNVERIFIED` — implemented private contract; not confirmed.
- `TESTED` — covered by an automated test.

## Security notes

- Passwords: PBKDF2-HMAC-SHA256 (600k iterations by default).
- Tokens/tickets: random opaque values; only SHA-256 hashes stored.
- Optional `GWSC_TOKEN_PEPPER`; changing it invalidates all tokens.
- Legacy plaintext tokens are invalidated on migration.
- Logs and captures redact credentials; captures are development-only.
- Shark-card claims require an explicit opt-in because a request cannot prove
  a purchase.

## Repository layout

```
gwsc_local_termux/   application package
  routes/            HTTP + native handlers
  data/              SQLite database (gitignored)
  logs/              logs (gitignored)
  backups/           backups (gitignored)
tests/               pytest suite
deploy/              systemd, nginx, env examples
docs/                architecture, protocol, operations, testing
Dockerfile           container image
compose.yaml         local container deployment
render.yaml          Render blueprint
```

## Limitations

- SQLite + a single process. Rate limits, SSE and the relay registry are
  in-memory and per-process.
- The UDP relay forwards opaque datagrams. It does not implement the game's
  handshake, encryption, NAT traversal or synchronization.
- No actual Android client compatibility has been demonstrated.
- No Prometheus/tracing; observability is `/health` plus logs.

## License / affiliation

Private experiment for a modified client. Not affiliated with or endorsed by
Rockstar Games. Grand Theft Auto is a trademark of its respective owners.
