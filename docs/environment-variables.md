# Environment Variables

All configuration is read from the environment **once at process start**.
Restart the server after changing values. Defaults are chosen for local
Termux development; production values are called out below.

Copy [`.env.example`](../.env.example) as a starting point. Never commit
`.env` files or real secrets.

## Identity and network

| Variable | Default | Notes |
| --- | --- | --- |
| `GWSC_ENV` | `development` | `production`/`prod` disables capture and docs by default. |
| `GWSC_SERVER_NAME` | `Unknown Beaches Social Club` | Shown in `/` and `/gwsc/api/info`. |
| `GWSC_SERVER_VERSION` | `1.0.0` | Informational. |
| `GWSC_HOST` | `0.0.0.0` | Bind address. Use `127.0.0.1` behind a proxy. |
| `GWSC_PORT` | `8443` | HTTP(S) port. `PORT` (Render/PaaS) overrides it. |
| `GWSC_RELAY_HOST` | `0.0.0.0` | UDP bind address. |
| `GWSC_RELAY_PORT` | `6672` | UDP relay port. `0` disables the listener (tests). |
| `GWSC_RELAY_PUBLIC_ADDRESS` | *(empty)* | Public address advertised by `GetRelayServers`. Required for relay discovery; empty returns an XML error. |
| `GWSC_RELAY_PUBLIC_PORT` | `GWSC_RELAY_PORT` | Public UDP port advertised by `GetRelayServers`. May differ from the bind port behind NAT. |
| `GWSC_PUBLIC_HOST` | *(empty)* | Public host advertised to matchmaking. Empty uses the caller's address. |

## TLS

| Variable | Default | Notes |
| --- | --- | --- |
| `GWSC_SSL_ENABLED` | `true` (false on Render) | Enable Uvicorn TLS. Disable when a proxy terminates TLS. |
| `GWSC_SSL_CERT` | `cert.pem` | Path relative to `gwsc_local_termux/`. |
| `GWSC_SSL_KEY` | `key.pem` | Path relative to `gwsc_local_termux/`. |

Certificates are generated automatically at startup when missing and TLS is
enabled. `*.pem` and `*.key` are gitignored.

## Storage

| Variable | Default | Notes |
| --- | --- | --- |
| `DATA_DIR` | `gwsc_local_termux/data` | Directory containing the database. |
| `GWSC_DB_NAME` | `gwsc.db` | Database filename inside `DATA_DIR`. |
| `GWSC_LOG_DIR` | `gwsc_local_termux/logs` | Log directory. |
| `GWSC_BACKUP_DIR` | `gwsc_local_termux/backups` | Backup directory. |

## Accounts and sessions

| Variable | Default | Notes |
| --- | --- | --- |
| `GWSC_REGISTRATION` | `true` | Allow new registrations. |
| `GWSC_PASSWORD_MIN_LENGTH` | `8` | Minimum password length. |
| `GWSC_PASSWORD_MAX_LENGTH` | `128` | Maximum password length. |
| `GWSC_NICKNAME_MIN_LENGTH` | `3` | Minimum nickname length. |
| `GWSC_NICKNAME_MAX_LENGTH` | `30` | Maximum nickname length. |
| `GWSC_TOKEN_EXPIRY` | `86400` | Token lifetime in seconds. |
| `GWSC_TOKEN_LAST_USED_UPDATE` | `60` | Minimum seconds between `last_used_at` writes. |
| `GWSC_TOKEN_PEPPER` | *(empty)* | Optional secret mixed into token hashes. **Keep stable**; changing it invalidates every token. |
| `GWSC_PBKDF2_ITERATIONS` | `600000` | Password hashing work factor. |

## Presence and sessions

| Variable | Default | Notes |
| --- | --- | --- |
| `GWSC_PRESENCE_TIMEOUT` | `300` | Seconds before presence expires. |
| `GWSC_PRESENCE_TOUCH_INTERVAL` | `30` | Minimum seconds between presence refreshes. |
| `GWSC_TICKET_EXPIRY` | `3600` | Native ticket/SC-token lifetime. Also the launch-ticket lifetime. |
| `GWSC_NATIVE_SERVICE_HOST` | *(empty)* | Opt-in native redirect emitted as `<S h=".."/>` in CreateTicket XML. Bare `host[:port]`, no scheme/path. Empty uses the launcher's `-rosHost=`. Invalid values fail at startup. |
| `GWSC_NATIVE_SERVICE_EP` | `*` | Wildcard pattern for `GWSC_NATIVE_SERVICE_HOST` (`ep` attribute). `*`/`?` are supported. |
| `GWSC_MAX_TICKETS_PER_ACCOUNT` | `10` | Retained ROS tickets per account (global native cap). |
| `GWSC_MAX_LAUNCH_TICKETS_PER_ACCOUNT` | `3` | Retained `game-launch` tickets per account; capped per service so launches cannot evict ROS tickets. |
| `GWSC_SESSION_TIMEOUT` | `300` | Matchmaking session heartbeat timeout. |
| `GWSC_MAX_SESSIONS_PER_HOST` | `5` | Active sessions per host account. |
| `GWSC_MAX_SESSION_PLAYERS` | `30` | Maximum players per session. |
| `GWSC_MATCHMAKING_RESULT_LIMIT` | `20` | Default/max find results (max 50). |
| `GWSC_NATIVE_MATCHMAKING_SESSION_SECONDS` | `28800` | Native `.asmx` advertisement lifetime (8 hours). |
| `GWSC_P2P_CERT_TTL` | `86400` | Issued P2P certificate lifetime in seconds. |
| `GWSC_P2P_CERT_DIR` | `<DATA_DIR>/certs/p2p` | Default directory for static P2P CA files. |
| `GWSC_P2P_CA_KEY` | `<P2P_CERT_DIR>/ca-key.pem` | Static P2P CA private key (0600). Rotate only with a client update. |
| `GWSC_P2P_CA_CERT` | `<P2P_CERT_DIR>/ca-cert.pem` | Static P2P CA certificate returned in `CertChain`. |

## Social

| Variable | Default | Notes |
| --- | --- | --- |
| `GWSC_MAX_MESSAGE_LENGTH` | `2000` | Message body limit. |
| `GWSC_MAX_GROUP_MEMBERS` | `50` | Group conversation size. |
| `GWSC_MAX_CONVERSATIONS_PER_REQUEST` | `50` | Conversation list cap. |
| `GWSC_MAX_MESSAGES_PAGE_SIZE` | `100` | Message page cap. |
| `GWSC_SSE_MAX_CONNECTIONS` | `5` | SSE connections per account. |
| `GWSC_SSE_HEARTBEAT` | `15` | SSE heartbeat seconds. |

## UDP relay

| Variable | Default | Notes |
| --- | --- | --- |
| `GWSC_RELAY_MAX_DATAGRAM` | `2048` | Maximum datagram size; larger packets are dropped. |
| `GWSC_RELAY_PEER_TIMEOUT` | `60` | Idle peer expiry in seconds. |
| `GWSC_RELAY_RATE_PPS` | `200` | Per-peer token refill rate. |
| `GWSC_RELAY_RATE_BURST` | `400` | Per-peer burst size. |
| `GWSC_RELAY_MAX_PEERS_PER_SESSION` | `32` | Session relay capacity. |
| `GWSC_RELAY_CERT_DIR` | `<DATA_DIR>/certs/relay` | Default directory for relay signing material. |
| `GWSC_RELAY_SIGNING_KEY` | `<RELAY_CERT_DIR>/relay_private_key.pem` | P-256 relay signing key (0600). May rotate independently. |
| `GWSC_RELAY_PUBLIC_KEY_DER` | `<RELAY_CERT_DIR>/relay_public_key.der` | SPKI DER public key. |
| `GWSC_RELAY_PUBLIC_KEY_PEM` | `<RELAY_CERT_DIR>/relay_public_key.pem` | SPKI PEM public key used for local verification. |
| `GWSC_RELAY_PUBLIC_KEY_TXT` | `<RELAY_CERT_DIR>/relay_public_key.txt` | Base64 SPKI key emitted by `GetRelayServers`. |

## Economy

| Variable | Default | Notes |
| --- | --- | --- |
| `GWSC_ALLOW_SHARKCARD_CLAIMS` | `false` | **Development only.** A request cannot prove a real purchase. |
| `GWSC_STARTER_PACK_AMOUNT` | `1000000` | Starter pack bank bonus. |
| `GWSC_MAX_ECONOMY_AMOUNT` | `100000000` | Bank balance ceiling. |

## Abuse limits

| Variable | Default | Notes |
| --- | --- | --- |
| `GWSC_MAX_BODY_BYTES` | `1048576` | Request body limit; larger requests get 413. |
| `GWSC_LOGIN_RATE_LIMIT` | `10` | Login attempts per window (per IP and per account). |
| `GWSC_LOGIN_RATE_WINDOW` | `300` | Login window in seconds. |
| `GWSC_REGISTER_RATE_LIMIT` | `5` | Registrations per window. |
| `GWSC_REGISTER_RATE_WINDOW` | `3600` | Registration window in seconds. |
| `GWSC_MESSAGE_RATE_LIMIT` | `30` | Messages per window. |
| `GWSC_MESSAGE_RATE_WINDOW` | `60` | Message window in seconds. |
| `GWSC_SESSION_RATE_LIMIT` | `10` | Session operations per window. |
| `GWSC_SESSION_RATE_WINDOW` | `60` | Session window in seconds. |
| `GWSC_GAME_LAUNCH_RATE_LIMIT` | `10` | Game-launch ticket requests per window (per IP and account). |
| `GWSC_GAME_LAUNCH_RATE_WINDOW` | `60` | Game-launch window in seconds. |
| `GWSC_RELAY_REGISTER_RATE_LIMIT` | `20` | Relay registrations per window. |
| `GWSC_RELAY_REGISTER_RATE_WINDOW` | `60` | Relay registration window. |

Rate limiting is **process-local** and in-memory. It resets on restart and is
not shared between workers.

## Development capture

| Variable | Default | Notes |
| --- | --- | --- |
| `GWSC_CAPTURE_ENABLED` | `false` | Forced off in production. |
| `GWSC_CAPTURE_DIR` | `gwsc_local_termux/captures` | Output directory (mode 0700). |
| `GWSC_CAPTURE_MAX_BODY` | `65536` | Maximum captured body bytes. |

Captured headers are allow-listed; passwords, tokens, cookies, tickets and
Social Club tokens are redacted. Captures can still contain personal data —
never commit them.

## Web

| Variable | Default | Notes |
| --- | --- | --- |
| `GWSC_ALLOWED_ORIGINS` | `*` | Comma-separated CORS origins. Credentials are disabled when `*` is used. |
| `GWSC_TRUST_PROXY` | `false` | Trust `X-Forwarded-For`/`X-Real-IP` from a proxy. |
| `GWSC_DOCS_ENABLED` | `true` in dev | Serves `/docs` and `/openapi.json`. Forced off in production. |
| `GWSC_DEBUG_ENDPOINTS` | `false` | Reserved for development diagnostics; forced off in production. |

## Maintenance

| Variable | Default | Notes |
| --- | --- | --- |
| `GWSC_MAINTENANCE_INTERVAL` | `30` | Seconds between cleanup passes. |

## Provider variables

| Variable | Effect |
| --- | --- |
| `PORT` | Overrides `GWSC_PORT`; also marks the environment as Render-like. |
| `RENDER` | Marks the environment as Render-like; disables TLS by default. |
