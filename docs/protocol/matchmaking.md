# Matchmaking Contract

**Status:** `UNVERIFIED` / `INFERRED`. The Android client's real
matchmaking task paths and field names have not been captured. The handlers
below preserve the original prototype's path shapes but enforce
authentication, ownership and capacity.

## Session model

| Field | Notes |
| --- | --- |
| `session_id` | Public opaque ID, 1–64 chars (`A-Z a-z 0-9 _ . : -`) |
| `host_account_id` | Internal account that owns the session |
| `host_rockstar_id` | Public host ID |
| `host_ip`, `host_port` | Advertised endpoint |
| `session_type` | e.g. `FreeMode` |
| `region` | Free-form region label |
| `visibility` | `public`, `friends`, `private` |
| `state` | `advertised`, `active`, `closed` |
| `max_players`, `current_players` | Capacity |
| `last_heartbeat` | Used for stale-session cleanup |

Session membership is stored in `session_members` and is unique per
`(session_id, rockstar_id)`.

## Lifecycle

```text
created -> advertised -> active -> closing -> closed
```

The maintenance worker closes sessions whose `last_heartbeat` is older than
`GWSC_SESSION_TIMEOUT` (default 300 s) and marks their members as `left`.

## Advertise

Patterns:

```text
/{prefix}MatchmakingAdvertiseTask
/{title}/{version}/{service}/MatchmakingAdvertise
```

Authentication: GWSC token (form/query/header).

Fields (`UNVERIFIED`):

| Field | Required | Notes |
| --- | --- | --- |
| `sessionId` | no | Generated if omitted |
| `port` / `hostPort` | no | Defaults to `GWSC_RELAY_PORT` |
| `maxPlayers` | no | Defaults to `GWSC_MAX_SESSION_PLAYERS` |
| `sessionType` | no | Defaults to `FreeMode` |
| `region` | no | Defaults to `unknown` |
| `visibility` | no | `public`, `friends`, `private` |

Rules:

- Only an authenticated account can advertise.
- The advertised host address is `GWSC_PUBLIC_HOST` when configured,
  otherwise the caller's address.
- A host may have at most `GWSC_MAX_SESSIONS_PER_HOST` active sessions.
- A session owned by another account cannot be overwritten (403).
- The host is inserted as a `joined` member.

## Find

Patterns:

```text
/{prefix}MatchmakingFindTask
/{title}/{version}/{service}/MatchmakingFind
```

Returns active/advertised sessions with a recent heartbeat. Optional
filters: `region`, `sessionType`, `limit` (max 50). Loopback and private
addresses are not returned to remote clients.

Response shape (`UNVERIFIED`):

```xml
<Response>
  <Status>1</Status>
  <Result>
    <SessionsCount>1</SessionsCount>
    <Session>
      <SessionId>...</SessionId>
      <HostGamerId>10001</HostGamerId>
      <HostIp>203.0.113.10</HostIp>
      <HostPort>6672</HostPort>
      <CurrentPlayers>1</CurrentPlayers>
      <MaxPlayers>30</MaxPlayers>
      <SessionType>FreeMode</SessionType>
      <Region>unknown</Region>
      <State>advertised</State>
    </Session>
  </Result>
</Response>
```

## Join

Patterns:

```text
/{prefix}MatchmakingJoinTask
/{title}/{version}/{service}/MatchmakingJoin
```

Rules:

- The session must exist, be active and not `closed`.
- Private sessions can only be joined by the host (until an invitation
  contract is observed).
- Capacity is enforced from `session_members` with `status = 'joined'`.
- Duplicate membership is idempotent.

## Unadvertise

Patterns:

```text
/{prefix}MatchmakingUnadvertiseTask
/{title}/{version}/{service}/MatchmakingUnadvertise
```

Only the host can unadvertise. The session becomes `closed`, members are
marked `left`, relay peers for the session are removed, and the in-memory
relay registry drops the session.

## Open questions

1. What are the real task paths and XML namespaces?
2. Does the client use `GetPresenceServerInfo` to discover a host/port?
3. Does the client expect a session list, a single session, or a lobby ID?
4. Are sessions advertised with a public IP supplied by the client?
5. How are private/friends-only sessions invited?
6. What heartbeat cadence does the client use?
