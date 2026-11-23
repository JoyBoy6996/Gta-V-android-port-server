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
                       |   lso lso_cloud lso_static|
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
| `database.py` | Schema (`SCHEMA_VERSION = 9`), connection defaults, forward-only migrations, active-player upsert/count helpers. |
| `auth.py` | Opaque token creation/lookup, expiry/revocation, FastAPI dependencies, native credential resolution. |
| `security.py` | PBKDF2 password hashing, SHA-256 token/ticket hashing, random token generation, process-local rate limiter. |
| `presence.py` | Heartbeat/expiry-based online state and the real online count. |
| `validators.py` | Email, nickname, password and message-body validation. |
| `xml_protocol.py` | Safe XML builders for native responses (no string concatenation). |
| `object_storage.py` | Account-scoped object keys, local filesystem backend, optional S3-compatible backend. |
| `static_assets.py` | Size- and SHA-256-verified immutable tunables, RPF and compatibility blobs. |
| `multiplayer_relay.py` | Session-aware UDP registry, token bucket, counters, lifecycle. |
| `maintenance.py` | Periodic cleanup loop and `run_cleanup_once()`. |
| `capture.py` | Development request/response capture, redaction, body limit, request IDs, access log. |
| `logging_utils.py` | Redaction filter for log records and exception text, including Discord credentials. |
| `backup.py` | SQLite online backup/restore with integrity checks. |
| `generate_certs.py` | Self-signed TLS certificate with SANs for local/LAN hosts. |
| `routes/lso.py`, `routes/lso_cloud.py`, `routes/lso_static.py` | LSO parity services: stateless/social/clan, S3-backed cloud routes, byte-exact static assets and safe redirects. |
| `routes/*.py` | Other HTTP and native endpoint handlers. |

## Request pipeline

1. **CORS** (outermost) so error responses still carry CORS headers.
2. **RequestID** assigns or sanitises `X-Request-ID`.
3. **AccessLog** logs method, path, status, duration and request ID.
4. **BodyLimit** rejects bodies larger than `GWSC_MAX_BODY_BYTES` with 413;
   cloud upload paths use the larger `GWSC_OBJECT_MAX_BYTES` boundary.
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
| `active_players` | Authenticated native platform observations, unique per `(rockstar_id, platform)`; feeds real player counts. |
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
- `require_user()` records an `active_players` observation after successful
  native authentication when the request supplies an allowlisted platform.
  Missing/unknown platforms are ignored and write failures never change the
  authentication result.

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
- **LSO parity**: `.asmx`, cloud, static asset and redirect surfaces adapted
  from the source-confirmed upstream server. Status is tracked in
  [`protocol/lso-parity-matrix.md`](protocol/lso-parity-matrix.md).

All native, relay and LSO contracts are `UNVERIFIED` against the real Android
client unless explicitly marked `TESTED` in the parity matrix; `TESTED` means
the backend behavior is covered by automated tests, not that the game accepted
it.

## Known limitations

- SQLite + a single process is a development/small-group design. See
  [`setup-vps.md`](setup-vps.md) for scaling notes.
- Rate limiting, SSE fan-out and the relay registry are in-memory and
  per-process.
- The relay does not parse game packets, implement a handshake or provide
  NAT traversal.
- Static asset redirects require explicitly configured HTTPS base URLs with
  allowlisted hostnames; unconfigured targets return 404. Dynamic PS3 tunables
  are `DELIBERATELY OMITTED` and return 501 rather than copying upstream's
  hardcoded crypto contract.
- Discord support is limited to inert configuration, real active-player count
  data and log redaction. The gateway bot is a separate external process and is
  not started or configured by FastAPI.
- Actual Android client compatibility has **not** been verified. No capture
  of a successful login, native task or gameplay session exists yet.
