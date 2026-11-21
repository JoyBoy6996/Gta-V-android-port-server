# GWSC REST Contract

All GWSC routes are available under `/gwsc/api/...` and, for backward
compatibility with the original prototype, without the prefix. New clients
should use the prefixed form.

## Authentication

Protected routes require:

```http
Authorization: Bearer <opaque-token>
```

Tokens are random opaque values prefixed with `gwsc_`. Only a SHA-256 hash
(optionally peppered with `GWSC_TOKEN_PEPPER`) is stored in `tokens`.
Expiry, revocation and account status are checked on every request.

- Default lifetime: `GWSC_TOKEN_EXPIRY` (86400 seconds).
- Logout revokes one token.
- Logout-all revokes every token and marks presence offline.
- Password change revokes every other session but keeps the current one.
- Expired tokens are deleted lazily on lookup and by the maintenance worker.

## Error shape

`UNVERIFIED` against the game parser. The server returns a stable object:

```json
{
  "error": "invalid_credentials",
  "message": "Invalid email or password",
  "detail": { "error": "invalid_credentials", "message": "Invalid email or password" }
}
```

Validation errors use HTTP 422 with `error: "invalid_request"` and a
`detail` list. Oversized bodies use HTTP 413.

## Accounts

### `POST /gwsc/api/register`

```json
{
  "email": "test@example.invalid",
  "nickname": "testuser",
  "password": "a long test password",
  "client": "launcher"
}
```

Rules:

- Email is normalised to lower case and validated.
- Nickname is 3–30 characters (`A-Z a-z 0-9 space . _ -`).
- Password length is `GWSC_PASSWORD_MIN_LENGTH`–`GWSC_PASSWORD_MAX_LENGTH`.
- Registration can be disabled with `GWSC_REGISTRATION=false`.
- Duplicate email/nickname returns HTTP 409.
- Password hashes are PBKDF2-HMAC-SHA256 with `GWSC_PBKDF2_ITERATIONS`
  (600,000 by default). Legacy `salt:hex` hashes are upgraded on login.

Response (contract `UNVERIFIED`):

```json
{
  "token": "gwsc_...",
  "profile": {
    "rockstarId": 10001,
    "nickname": "testuser",
    "email": "test@example.invalid",
    "avatar": "preset_0",
    "crewTag": "",
    "online": true
  }
}
```

### `POST /gwsc/api/login`

Request shape is `OBSERVED`:

```json
{ "email": "test@example.invalid", "password": "secret", "client": "launcher" }
```

Email or nickname may be used as the login identifier. Invalid credentials
always return the same generic 401 response. Disabled accounts return 403.
Login is rate-limited per IP and per account.

### `GET /gwsc/api/me`

Returns the authenticated account's profile. Email is included here but is
never included in another account's public profile.

### `PATCH /gwsc/api/me`

```json
{ "nickname": "newname" }
```

### `POST /gwsc/api/me/password`

```json
{ "current": "old password", "next": "new password" }
```

The current password is required. Other sessions are revoked.

### Avatar presets

- `GET /gwsc/api/avatars/presets` returns `colours` and `presets`.
- `POST /gwsc/api/me/avatar/preset` accepts `{ "preset": "preset_3" }` or a
  listed colour value.

## Public profiles and blocking

`GET /gwsc/api/users/{rockstarId}/profile` resolves only by public
Rockstar ID or nickname. Internal database IDs are not accepted. If the
target has blocked the requester, the route returns 404 to avoid leaking
profile data.

`POST /gwsc/api/users/{rockstarId}/block` and
`DELETE /gwsc/api/users/{rockstarId}/block` manage the block relationship.
Blocking prevents direct conversations and message delivery in both
directions.

## Friends

Relationship states are `pending`, `accepted`, `rejected`, `blocked`.
Direction is inferred from the row: `user_id` is the requester,
`friend_id` is the recipient.

- `GET /gwsc/api/friends` returns `friends`, `incoming`, `outgoing`, `blocked`.
- `POST /gwsc/api/friends` with `{ "nickname": "..." }` sends a request.
  If the other account already sent a request, it is accepted automatically.
- `POST /gwsc/api/friends/{rockstarId}/accept` only accepts an incoming
  pending request.
- `POST /gwsc/api/friends/{rockstarId}/reject` rejects an incoming request.
- `DELETE /gwsc/api/friends/{rockstarId}` removes a friend or cancels an
  outgoing request. Blocked relationships must be unblocked instead.

Self-friending and duplicate relationships are rejected.

## Conversations and messages

- Direct conversations are unique per account pair.
- Group conversations have a creator; only the creator can remove other
  members, while any member can leave.
- Only conversation members can read or send messages.
- Direct conversations are unavailable while either account has blocked
  the other.
- Message bodies are trimmed, limited to `GWSC_MAX_MESSAGE_LENGTH`, and
  control characters are rejected.
- Sending is rate-limited per account.
- Message history supports `limit` and `before_id` pagination.

## SSE events

`GET /gwsc/api/events` requires a bearer token (header or `?token=`). The
stream is `text/event-stream` with heartbeat comments. Connections are
per-account, limited by `GWSC_SSE_MAX_CONNECTIONS`, and never receive
another account's events.

Event names implemented (payload schema `UNVERIFIED`):

- `message` — new message in a conversation.
- `friends` — incoming request, accepted, rejected or removed.

## Economy

- `GET /gwsc/api/sharkcards` returns the catalog, bank balance and starter
  pack state.
- Shark card claims are **disabled by default**. A client request cannot
  prove a real-money purchase. Enable only on a private development server
  with `GWSC_ALLOW_SHARKCARD_CLAIMS=true`.
- Starter pack claims are atomic and one-time. Ownership is recorded in
  `starter_pack_claims` and in a transaction ledger.
- All balance mutations are server-side and recorded in `transactions`.

## Limits

| Setting | Default |
| --- | --- |
| `GWSC_MAX_BODY_BYTES` | 1 MiB |
| `GWSC_LOGIN_RATE_LIMIT` / window | 10 / 300 s |
| `GWSC_REGISTER_RATE_LIMIT` / window | 5 / 3600 s |
| `GWSC_MESSAGE_RATE_LIMIT` / window | 30 / 60 s |
| `GWSC_MAX_MESSAGE_LENGTH` | 2000 |
| `GWSC_MAX_GROUP_MEMBERS` | 50 |
| `GWSC_SSE_MAX_CONNECTIONS` | 5 per account |

Rate limiting is process-local. A multi-worker deployment enforces the
limit per worker; use a shared limiter if that is not acceptable.
