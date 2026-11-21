# GTA V Android Backend — Protocol Inventory

**Status:** living document. The Android client's observed traffic is
authoritative. Anything not captured from the game is labelled
`SOURCE_CONFIRMED`, `INFERRED`, or `UNVERIFIED`.

## Evidence labels

| Label | Meaning |
| --- | --- |
| `OBSERVED` | Captured from the actual Android game or verified in client code. |
| `SOURCE_CONFIRMED` | Found in static inspection / symbols, not yet observed in traffic. |
| `INFERRED` | Reasoned hypothesis that still needs verification. |
| `UNVERIFIED` | Placeholder or unknown. Never treat as compatible. |
| `TESTED` | Verified by an automated test (the test is named). |

## First observed request

`OBSERVED` — the Android client has been seen sending:

```http
POST /gwsc/api/login
Content-Type: application/json
Accept: application/json
```

```json
{
  "email": "<test-account-email>",
  "password": "<redacted>",
  "client": "launcher"
}
```

The request reached the local HTTPS server on port 8443. This proves
connectivity and routing, **not** successful authentication compatibility.

## Route inventory

### GWSC REST

| Method | Path | Auth | Request | Response (implemented) | Evidence | Test |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/gwsc/api/info` | none | — | `name, version, server, status, accounts, online, registration` | `OBSERVED` (candidate) | `test_info_reports_zero_online_when_empty` |
| POST | `/gwsc/api/register` | none | `email, nickname, password, client?` | `token, profile` | `UNVERIFIED` | `test_register_login_and_profile` |
| POST | `/gwsc/api/login` | none | `email, password, client?` | `token, profile` | request `OBSERVED`; response `UNVERIFIED` | `test_register_login_and_profile` |
| POST | `/gwsc/api/logout` | bearer (optional) | — | `status` | `UNVERIFIED` | `test_revoked_token_is_rejected` |
| POST | `/gwsc/api/logout-all` | bearer | — | `status` | `UNVERIFIED` | `test_logout_all_revokes_every_session` |
| GET | `/gwsc/api/me` | bearer | — | profile object | `UNVERIFIED` | `test_register_login_and_profile` |
| PATCH | `/gwsc/api/me` | bearer | `nickname` | profile object | `UNVERIFIED` | — |
| POST | `/gwsc/api/me/password` | bearer | `current, next` | `status` | `UNVERIFIED` | `test_password_change_requires_current_and_revokes_other_sessions` |
| GET | `/gwsc/api/avatars/presets` | none | — | `colours, presets` | `UNVERIFIED` | — |
| POST | `/gwsc/api/me/avatar/preset` | bearer | `preset` | `status, avatar` | `UNVERIFIED` | — |
| GET | `/gwsc/api/users/{rockstarId}/profile` | bearer | — | public profile | `UNVERIFIED` | `test_blocking_hides_profile_and_prevents_messages` |
| POST | `/gwsc/api/users/{rockstarId}/block` | bearer | — | `status` | `UNVERIFIED` | `test_blocking_hides_profile_and_prevents_messages` |
| DELETE | `/gwsc/api/users/{rockstarId}/block` | bearer | — | `status` | `UNVERIFIED` | — |
| GET | `/gwsc/api/friends` | bearer | — | `friends, incoming, outgoing, blocked` | `UNVERIFIED` | `test_friend_request_accept_flow` |
| POST | `/gwsc/api/friends` | bearer | `nickname` | `status, message` | `UNVERIFIED` | `test_friend_request_accept_flow` |
| POST | `/gwsc/api/friends/{rockstarId}/accept` | bearer | — | `status` | `UNVERIFIED` | `test_friend_request_accept_flow` |
| POST | `/gwsc/api/friends/{rockstarId}/reject` | bearer | — | `status` | `UNVERIFIED` | — |
| DELETE | `/gwsc/api/friends/{rockstarId}` | bearer | — | `status` | `UNVERIFIED` | — |
| GET | `/gwsc/api/conversations` | bearer | — | `conversations[]` | `UNVERIFIED` | — |
| POST | `/gwsc/api/conversations` | bearer | `rockstarId` or `rockstarIds, name?` | conversation object | `UNVERIFIED` | `test_conversation_membership_is_enforced` |
| GET | `/gwsc/api/conversations/{id}/messages` | bearer | `limit?, before_id?` | `messages[]` | `UNVERIFIED` | `test_conversation_membership_is_enforced` |
| POST | `/gwsc/api/conversations/{id}/messages` | bearer | `body` | message object | `UNVERIFIED` | `test_message_validation_and_rate_limit` |
| POST | `/gwsc/api/conversations/{id}/read` | bearer | `lastId?` | `status` | `UNVERIFIED` | — |
| DELETE | `/gwsc/api/conversations/{id}/members/{rockstarId}` | bearer | — | `status` | `UNVERIFIED` | `test_group_member_removal_authorization` |
| GET | `/gwsc/api/sharkcards` | bearer | — | `wallet, bank, cards, starterPack` | `UNVERIFIED` | — |
| POST | `/gwsc/api/sharkcards/{cardId}` | bearer | `Idempotency-Key?` | transaction result | `UNVERIFIED`; disabled by default | — |
| GET | `/gwsc/api/starterpack` | bearer | — | claim state | `UNVERIFIED` | — |
| POST | `/gwsc/api/starterpack` | bearer | `Idempotency-Key?` | transaction result | `UNVERIFIED` | — |
| GET | `/gwsc/api/events` | bearer or `?token=` | — | `text/event-stream` | `SOURCE_CONFIRMED` (SSE suspected) | `test_sse_requires_authentication` |

All routes are also mounted without the `/gwsc/api` prefix for backward
compatibility with the original prototype (for example `/login`,
`/friends`). The prefix is the documented form.

### Relay registration (private extension)

| Method | Path | Auth | Request | Response | Evidence |
| --- | --- | --- | --- | --- | --- |
| POST | `/gwsc/api/relay/register` | bearer | `sessionId, udpPort` | `sessionId, address, peerTimeoutSeconds` | `INFERRED` |
| DELETE | `/gwsc/api/relay/register` | bearer | — | `status, removed` | `INFERRED` |
| GET | `/gwsc/api/relay/status` | bearer | — | own peers + registry counts | `INFERRED` |

## Native identifiers from static inspection

These are discovery leads, not endpoint specifications:

```text
GetScAuthToken
CreateScAuthToken
ValidatePassword
GameTransactions
GetCatalog
GetCatalogVersion
ros.ticket.renew
ros.ticket.invalidate
GetPresenceServerInfo
CommitPresence
```

Implemented native paths are documented in
[`native-ros.md`](native-ros.md) and [`matchmaking.md`](matchmaking.md).

## Response contract status

| Contract | Status | Notes |
| --- | --- | --- |
| Login request fields | `OBSERVED` | `email`, `password`, `client` |
| Login success response | `UNVERIFIED` | Implemented as `{token, profile}`; must be confirmed with a client capture |
| Login error response | `UNVERIFIED` | Stable `{error, message, detail}` object |
| Profile fields | `UNVERIFIED` | Public fields only; email only on `/me` |
| SSE event names/payloads | `UNVERIFIED` | Per-account `message` and `friends` events |
| CreateTicket XML | `UNVERIFIED` | Deterministic XML, no namespaces invented |
| Matchmaking XML | `UNVERIFIED` | Private session advertisement |
| Relay packet framing | `UNVERIFIED` | The relay forwards opaque datagrams; it does not parse game packets |

## Open questions

1. Does the client require a specific login success field name or nesting?
2. Does the client send `Accept: application/json` on every GWSC call?
3. Which HTTP status codes map to which launcher error strings?
4. Does the client validate the self-signed certificate, and is pinning present?
5. Does the client use SSE, polling, or both?
6. What exact XML namespaces/root elements do native tasks expect?
7. Are native tickets used as bearer credentials for other services?
8. What is the real matchmaking task path and field set?
9. What UDP registration handshake, if any, does the game use?
10. Does the client expect a particular Rockstar ID format beyond a positive integer?

## Capturing more evidence

Enable development capture and replay one action at a time:

```bash
export GWSC_CAPTURE_ENABLED=true
./run.sh
```

Captures are written to `gwsc_local_termux/captures/captures.jsonl` with
mode `0600`. Passwords, bearer tokens, cookies, tickets and Social Club
tokens are replaced with `***`. Never commit real captures containing
personal data.
