# Architecture

This document describes what the backend actually does today. It separates
implemented behaviour from protocol assumptions. The Android client's exact
wire contract is still `UNVERIFIED`; see
[`protocol/ANDROID_PROTOCOL_INVENTORY.md`](protocol/ANDROID_PROTOCOL_INVENTORY.md).

## Goals and non-goals

**Goals**

- A private, self-hosted backend for a modified GTA V Android client.
- Correct authentication, authorization, validation and rate limiting on
  every endpoint.
- Honest failure modes: no fabricated users, tickets, balances or online
  counts.
- A Termux-first developer workflow that also runs in Docker / on a VPS.

**Non-goals**

- Claiming Rockstar Online compatibility. Nothing here is affiliated with
  Rockstar Games.
- Reimplementing the game's gameplay networking, encryption, anti-cheat or
  economy simulation. The UDP relay forwards opaque datagrams only.
- Shipping or building APKs. This repository is backend-only.

## Process model

```
                       +---------------------------+
   Android client ---> |  FastAPI / Uvicorn        |
   HTTPS 8443          |  (gwsc_local_termux)      |
                       |                           |
                       |  middleware:              |
                       |   CORS                    |
                       |   RequestID               |
                       |   AccessLog               |
                       |   BodyLimit               |
                       |   Capture (dev only)      |
                       |                           |
                       |  routers:                 |
                       |   info  auth  profile     |
                       |   friends messages        |
                       |   sharkcards events       |
                       |   relay  ros              |
                       +------------+--------------+
                                    |
                     +--------------+---------------+
                     |                              |
              +------v------+                +------v------+
              |  SQLite     |                | UDP relay   |
              |  gwsc.db    |                | :6672       |
              |  (WAL)      |                | in-memory   |
              +-------------+                +-------------+
```

- One Uvicorn process by default. Rate limiting and the SSE event bus are
  process-local; a multi-worker deployment must account for that.
- A background maintenance task (asyncio) expires presence records,
  sessions, tokens and tickets, and prunes relay peers.
- The UDP relay runs as an asyncio datagram protocol in the same event loop.

## Module map

| Module | Responsibility |
| --- | --- |
| `server.py` | App assembly, middleware order, lifespan, exception handlers, `/` and `/health`. |
| `config.py` | Every environment variable and default. Read once at import. |
| `database.py` | Schema (`SCHEMA_VERSION = 6`), connection defaults, forward-only migrations. |
| `auth.py` | Opaque token creation/lookup, expiry/revocation, FastAPI dependencies, native credential resolution. |
| `security.py` | PBKDF2 password hashing, SHA-256 token/ticket hashing, random token generation, process-local rate limiter. |
| `presence.py` | Heartbeat/expiry-based online state and the real online count. |
| `validators.py` | Email, nickname, password and message-body validation. |
| `xml_protocol.py` | Safe XML builders for native responses (no string concatenation). |
| `multiplayer_relay.py` | Session-aware UDP registry, token bucket, counters, lifecycle. |
| `maintenance.py` | Periodic cleanup loop and `run_cleanup_once()`. |
| `capture.py` | Development request/response capture, redaction, body limit, request IDs, access log. |
| `logging_utils.py` | Redaction filter for log records and exception text. |
| `backup.py` | SQLite online backup/restore with integrity checks. |
| `generate_certs.py` | Self-signed TLS certificate with SANs for local/LAN hosts. |
| `routes/*.py` | HTTP and native endpoint handlers. |

## Request pipeline

1. **CORS** (outermost) so error responses still carry CORS headers.
2. **RequestID** assigns or sanitises `X-Request-ID`.
3. **AccessLog** logs method, path, status, duration and request ID.
4. **BodyLimit** rejects bodies larger than `GWSC_MAX_BODY_BYTES` with 413.
5. **Capture** (development only) writes a redacted JSONL record.
6. **Router** validates input, authenticates, authorizes and runs SQL.
7. **Exception handlers** normalise errors to a stable JSON shape.

Middleware order matters: the first `add_middleware` call is the outermost
layer.

## Data model

| Table | Purpose |
| --- | --- |
| `users` | Account identity, profile, status, economy balances, starter-pack flag. |
| `tokens` | SHA-256 token hashes, expiry, device, last use. |
| `friends` | Directed relationship rows with status `pending`/`accepted`/`rejected`/`blocked`. |
| `conversations`, `conversation_members`, `messages` | Direct and group chat. |
| `achievements` | Per-account achievement unlocks shown on public profiles. |
| `game_sessions`, `session_members` | Matchmaking advertisements and membership. |
| `presence_records` | Heartbeat/expiry presence, one row per account. |
| `service_tickets` | Hashed native tickets bound to account + service. |
| `relay_peers` | Authenticated UDP endpoint registrations. |
| `transactions`, `starter_pack_claims` | Server-side economy ledger. |
| `account_status_history` | Audit trail for status changes. |
| `schema_migrations` | Applied migration versions. |

Connections enable `foreign_keys`, `busy_timeout`, WAL and
`synchronous=NORMAL`. The database file is chmod `0600` on init.

## Authentication and authorization

- Passwords: PBKDF2-HMAC-SHA256, `GWSC_PBKDF2_ITERATIONS` (600,000 default).
  Legacy `salt:hex` hashes verify and are upgraded on the next login.
- Tokens: opaque `gwsc_...` values. Only SHA-256 hashes are stored. Optional
  `GWSC_TOKEN_PEPPER` adds defence in depth.
- Every protected request checks: token exists, not expired, account
  `status = 'active'`.
- At most 20 concurrent tokens per account; oldest are pruned.
- Logout revokes one token; logout-all revokes all; password change revokes
  all *other* sessions.
- Native handlers accept the token from form/query/header/`Authorization`.
  There is no fallback account.

Authorization is explicit per route: conversation membership, session
host/member checks, block relationships, ticket ownership and resource
ownership are all enforced in SQL/handler logic. See the tests for the
covered paths.

## Protocol surfaces

- **GWSC REST/SSE** (`/gwsc/api/*`): accounts, profiles, friends, messages,
  economy, events. Documented in [`protocol/gwsc.md`](protocol/gwsc.md).
- **Native ROS** (unprefixed paths): tickets, presence, settings. Documented
  in [`protocol/native-ros.md`](protocol/native-ros.md).
- **Matchmaking**: advertise/find/join/unadvertise. Documented in
  [`protocol/matchmaking.md`](protocol/matchmaking.md).
- **UDP relay**: authenticated registration + opaque forwarding. Documented
  in [`protocol/multiplayer-transport.md`](protocol/multiplayer-transport.md).

All native and relay contracts are `UNVERIFIED` against the real client.

## Known limitations

- SQLite + a single process is a development/small-group design. See
  [`setup-vps.md`](setup-vps.md) for scaling notes.
- Rate limiting, SSE fan-out and the relay registry are in-memory and
  per-process.
- The relay does not parse game packets, implement a handshake or provide
  NAT traversal.
- Actual Android client compatibility has **not** been verified. No capture
  of a successful login, native task or gameplay session exists yet.
