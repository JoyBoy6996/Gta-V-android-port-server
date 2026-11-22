# PS4 server vs private Android backend

This compares Los Santos Online (an open-source PS4/PS5/Xbox/PC ROS server) with
this FastAPI backend. It is a **protocol comparison**, not a port. LSO is
`SOURCE_CONFIRMED` from its repository; Android rows are `SOURCE_CONFIRMED` only
where static inspection of the installed launcher or `libgtav.so` proves them.

## High-level comparison

| Area | Los Santos Online (PS4 family) | This backend | Evidence / status |
| --- | --- | --- | --- |
| Runtime | Node/Express, Prisma, PostgreSQL | Python/FastAPI, SQLite | Deliberate design choice; no wholesale port |
| Transport crypto | Platform-key RC4 middleware for ROS requests; complete-certificate TLS pinning | Plain HTTP(S) JSON/XML; self-signed TLS launcher setting; `-rlsslnocert` native flag | LSO `SOURCE_CONFIRMED`; Android uses AES for ticket keys and XOR+base64 only for User-Agent (`SOURCE_CONFIRMED`); no Android ROS RC4 path found (`INFERRED`) |
| Launcher token | Not part of PS4 flow | 43-char launch ticket from `/gwsc/api/game-launch`, stored hashed | Android `SOURCE_CONFIRMED` |
| Native credential | `Ticket` / `SessionTicket` form fields, `ros-SessionTicket` headers, SC auth token | Same credential names accepted; all resolve to hashed account-bound service tickets | LSO + native strings `SOURCE_CONFIRMED`; Android request order `UNVERIFIED` |
| Privileges | Platform-specific entitlement data | Explicitly omits ban privilege 7; grants 3/5 and safe defaults | Android `SOURCE_CONFIRMED`, `TESTED` |
| Service hosts | Configurable ROS host map in `CreateTicket` | Opt-in `<Services><Hosts><S .../>`, default off to honor launcher `-rosHost=` | Android parser `SOURCE_CONFIRMED`, `TESTED` |
| Matchmaking | Client-supplied `attrsJson.Data` stored and echoed in `Find` | Same: exact `Data` in `advertised_data`, echoed in `<R><Data>`; 8-hour expiry | LSO `SOURCE_CONFIRMED`; Android parser `SOURCE_CONFIRMED`, acceptance `UNVERIFIED` |
| P2P certificates | Static local CA signs client's uncompressed P-256 key; returns two-cert chain | Same shape using local OpenSSL CA and `-force_pubkey`; 0600 keys; client `sig` bounded but unverified | LSO `SOURCE_CONFIRMED`, `TESTED`; Android `UNVERIFIED` |
| Relay discovery | SPKI public key + ECDSA-SHA256 raw `r||s` signature over `host:port` | Same shape; relay key generated locally and independently rotatable | LSO `SOURCE_CONFIRMED`, `TESTED`; Android `UNVERIFIED` |
| Presence | Redis/DB-backed presence with attributes | SQLite `presence_records` and `/gwsc/api/info` online count | Independent implementation |
| Secrets | Keys/certs in `certs/` | Hashes only in DB; P2P/relay private keys local with `0600`; no captures or tokens logged | `TESTED` |

## What was adopted from LSO

1. **Opaque matchmaking `Data`.** The server never invents a `netPeerAddress`
   record. It stores exactly what the advertiser sends and returns it to
   `Find` callers that satisfy slot, platform and expiry filters.
2. **Credential names.** `ticket`, `SessionTicket`, `ros-SessionTicket`,
   `scAuthToken` and SC-auth token responses follow LSO/native naming. Every
   value is still a private, hashed-at-rest GWSC ticket.
3. **P2P CA model.** A static local CA issues short-lived certificates from
   the client's public key. No remote issuer and no private key in responses.
4. **Relay signature model.** The advertised `host:port` is signed with a
   local P-256 key; the public SPKI key and raw `r||s` signature are emitted in
   LSO's XML attributes.
5. **Eight-hour native advertisement lifetime**, separate from the REST
   session heartbeat timeout.

## What was deliberately not copied

- **RC4 request middleware.** LSO uses RC4 for PS4-family ROS requests, but the
  installed Android client's credential path shows AES (`rage::AES::CreateCloudAes`)
  for ticket keys and only a 4-byte XOR+base64 User-Agent header. Implementing
  RC4 would be a protocol invention on Android (`UNVERIFIED`).
- **Node/Prisma/PostgreSQL/Redis.** The private backend remains
  Python/FastAPI + SQLite as required.
- **PS4-only endpoints such as `CreateP2PCertificateCyprus`.** They are not
  present in the Android static evidence, so they are not exposed.
- **Generic success fallbacks.** Unknown native methods still return 404;
  every new endpoint validates input, authenticates the caller and has tests.

## Launcher-to-game sign-in gap

`SOURCE_CONFIRMED`: the launcher validates the `/game-launch` ticket with
`[A-Za-z0-9_-]{43}`, passes it as the `gwscLaunchToken` intent extra, and
`GTAVNativeActivity` sets `GTAV_LAUNCH_TOKEN` / `GTAV_LAUNCH_EMAIL` in the
environment. `onlineAvailable()` in the launcher depends on its own
`signedIn()` state.

However, no literal `GTAV_LAUNCH_TOKEN` was found in any inspected native
library, and no static xref connects it to `CreateTicketScAuthToken`,
`CreateScAuthToken`, `platformCredentials+0x65` or the native `ticket` field.
The native Online screen therefore appears to maintain its own Social Club
sign-in state (`INFERRED`). This is the most plausible explanation for
"launcher logged in, game Online option still shows login".

No server-side change can honestly claim to fix that symptom without one of:

- a redacted FastAPI access log covering the moment Online/login is tapped; or
- a captured/redacted native request showing the credential field it uses; or
- static evidence that the native UI reads `GTAV_LAUNCH_TOKEN`.

Until then, the server accepts every observed credential spelling and returns
real account-bound credentials, but native sign-in remains `UNVERIFIED`.

## Verification commands

```sh
GWSC_SSL_ENABLED=false GWSC_RELAY_PORT=0 \
  gwsc_local_termux/.venv/bin/python -m pytest -q
gwsc_local_termux/.venv/bin/python -m compileall -q gwsc_local_termux tests
git diff --check
```
