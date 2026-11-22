# Native ROS / Social Club Services

**Every contract in this document is `UNVERIFIED` unless explicitly marked
otherwise.** The XML field names, `.asmx` method names and ban flag below are
`SOURCE_CONFIRMED` by static inspection of the installed client
(`analysis/lib/arm64-v8a/libgtav.so` and the launcher DEX). The matchmaking,
P2P certificate and relay shapes are `SOURCE_CONFIRMED` from Los Santos
Online's PS4 server source, not from an Android capture. Automated coverage is
`TESTED`. No complete post-launch traffic capture exists, so the exact request
order and Android acceptance of those response shapes remain `UNVERIFIED`.

See [PS4 server vs private Android backend](ps4-server-comparison.md) for the
LSO comparison and the current launcher-to-native sign-in gap.

## XML layer

`gwsc_local_termux/xml_protocol.py` builds XML with `xml.etree.ElementTree`
(no string concatenation, correct escaping). Responses use:

```xml
<?xml version="1.0" encoding="utf-8"?>
<Response>
  <Status>1</Status>
  <Result>
    ...
  </Result>
</Response>
```

Errors use HTTP 4xx/5xx **and** a protocol body:

```xml
<?xml version="1.0" encoding="utf-8"?>
<Response>
  <Status>0</Status>
  <Error>
    <Code>401</Code>
    <Message>A valid account token is required</Message>
  </Error>
</Response>
```

Namespaces are deliberately omitted because none have been observed.

## Authentication

Native handlers accept a GWSC token from any of:

- form field `scAuthToken`, `authToken`, `token` or `ticket`;
- form field `sessionTicket` / `SessionTicket`;
- query parameter `scAuthToken` or `token`;
- `Authorization: Bearer ...`;
- `X-GWSC-Token` header;
- `ros-SessionTicket`, `ros-Session-Ticket` or `x-ros-sessionticket` header.

`ticket` is `SOURCE_CONFIRMED` for LSO's native matchmaking, P2P and many
Social Club calls. It is deliberately **not** accepted by the legacy
`/ros/ticket/renew` and `/ros/ticket/invalidate` account routes, where the same
field names the ticket being operated on. `SessionTicket` headers follow LSO's
post-auth convention; the exact Android header spelling is `UNVERIFIED`.

There is **no fallback to the latest online user** and no default account.

### Launcher launch tickets

The launcher calls `POST /gwsc/api/game-launch` with its bearer token and
receives `{"ticket": "<43 URL-safe characters>"}` (`SOURCE_CONFIRMED` from the
installed client). The ticket is account-bound and stored only as a hash with
`service = "game-launch"`.

The installed DEX sets `GTAV_LAUNCH_TOKEN` from the launcher intent extra
`gwscLaunchToken`, and the native create-ticket task posts its credential as
`scAuthToken=<token>` (`SOURCE_CONFIRMED`). Native handlers therefore accept a
launch ticket through the credential fields above. It expires with
`GWSC_TICKET_EXPIRY` (default 3600 s) and is capped at
`GWSC_MAX_LAUNCH_TICKETS_PER_ACCOUNT` (default 3) per account. It is **not**
accepted as a launcher REST session token. The first native request after game
start, and whether any intermediate token exchange occurs, remain
`UNVERIFIED`.

### `SessionTicket` credentials (`TESTED`)

`CreateTicket` returns a second private `SessionTicket` alongside `Ticket`
(`SOURCE_CONFIRMED` from the native parser). LSO uses it for post-auth calls;
this backend accepts it from `ros-SessionTicket` headers and from the
`sessionTicket` form field. It resolves through the same hashed,
account-bound ticket store as launch tickets and never becomes a launcher REST
session. `tests/test_native_auth.py` covers the header path
(`TESTED`).

## Native ban check (`SOURCE_CONFIRMED`, `TESTED`)

`CNetwork::CheckNetworkAccess` calls `CLiveManager::HasRosPrivilege(7)`.
When privilege 7 is present, the native function returns `15`
(`ACCESS_DENIED_BANNED`); privilege 16 is the separate suspended result. The
credential parser builds the privilege bitmask from the comma-separated
`<Privileges>` text. The optional `<Privs><i .../></Privs>` list is parsed but
is **not** what `HasPrivilege` reads.

Therefore server-issued credentials deliberately:

- omit privilege 7 (never grant the client's banned flag);
- grant privileges 3 and 5 (online/standard access);
- avoid the client's own unset defaults 12 and 20-27.

This is covered by `tests/test_native_auth.py` (`TESTED`). It fixes the
previous empty/zero privilege XML, which left the native credentials object
without a granted privilege mask and produced the banned screen.

## Native `.asmx` credential paths

`rlRosHttpTask::GetServicePath` formats `title/version/serviceSet/method` as
`"%s/%d/%s/%s"`. The create-ticket task posts `platformName=pcros` and
`scAuthToken=<token>` (`SOURCE_CONFIRMED`). The following literal methods are
registered (`TESTED`); the service/method constants are captured in route
wrappers and are never read from client input.

Ticket credentials:

```text
/{title}/{version}/{serviceSet}/auth.asmx/CreateTicketScAuthToken
/{title}/{version}/{serviceSet}/auth.asmx/CreateTicketSc3
/{title}/{version}/{serviceSet}/auth.asmx/CreateTicket
/{title}/{version}/{serviceSet}/socialclub.asmx/CreateTicketScAuthToken
```

SC auth tokens:

```text
/{title}/{version}/{serviceSet}/auth.asmx/CreateScAuthToken
/{title}/{version}/{serviceSet}/socialclub.asmx/CreateScAuthToken
/{title}/{version}/{serviceSet}/socialclub.asmx/GetScAuthToken
```

Each method also has an unprefixed alias, e.g.
`/auth.asmx/CreateTicketScAuthToken`. Unknown `.asmx` methods return 404; there
is no generic success handler.

`title`, `version` and `serviceSet` are validated against conservative regexes
for the prefixed form. A token from another account is rejected with 401 and
an XML error body.

## CreateTicket response (`SOURCE_CONFIRMED`, `TESTED`)

The parser `rlRosXmlCreateTicketResponseParser` and
`rlRosCreateTicketResponseHelper::ApplyCreateTicketResponse` read the
following fields. Names, base64 32-byte `SessionKey`, comma-separated
`Privileges` and nested `RockstarAccount` are `SOURCE_CONFIRMED`; the values
are private server credentials, not Rockstar data.

```xml
<Response>
  <Status>1</Status>
  <Result>
    <Ticket>gwsc_ticket_...</Ticket>
    <SecsUntilExpiration>3600</SecsUntilExpiration>
    <Region>1</Region>
    <PlayerAccountId>10001</PlayerAccountId>
    <Privileges>0,1,2,3,4,5,6,8,9,10,11,13,14,15,16,17,18,19,21,22</Privileges>
    <DisabledSecurityFlags>0</DisabledSecurityFlags>
    <SessionId>1795315200000</SessionId>
    <SessionKey>BASE64_32_BYTES</SessionKey>
    <SessionTicket>gwsc_ticket_...</SessionTicket>
    <CloudKey>BASE64_32_BYTES</CloudKey>
    <PublicIp>127.0.0.1</PublicIp>
    <PosixTime>1795315200</PosixTime>
    <IsSubAccount>false</IsSubAccount>
    <RockstarAccount>
      <RockstarId>10001</RockstarId>
      <Age>21</Age>
      <Nickname>player</Nickname>
      <Email>player@example.invalid</Email>
      <!-- AvatarUrl, CountryCode, LanguageCode, ZipCode are also emitted -->
    </RockstarAccount>
    <Privs>
      <i id="7" g="false" ed="0"/>
      <!-- one entry per known privilege id -->
    </Privs>
  </Result>
</Response>
```

### Optional service-host redirect (`SOURCE_CONFIRMED` schema, `TESTED`)

`rlRosXmlCreateTicketResponseParser::ParseServiceHosts` reads a
`<Services Count="N"><Hosts><S ep="..." h="..."/></Hosts></Services>` block.
`ep` is a `StringWildcardCompare` pattern (`*`/`?` are wildcards) and `h` is a
bare `host[:port]` copied into a 127-byte native buffer. `rlRosJsonHttpTask::
GetUrlHostName` checks these hosts before the compiled default
(`roscaelifer.thevakhovske.eu.org`).

The installed launcher already appends `-rosHost=<host>` to the game command
line (`SOURCE_CONFIRMED` from the DEX), so the override is **opt-in** and emits
nothing by default. Set it only when that launcher value is wrong or cannot be
changed:

```sh
GWSC_NATIVE_SERVICE_HOST=your-public-host:8443
GWSC_NATIVE_SERVICE_EP='*'   # default: match every native ROS endpoint
```

`service_hosts()` rejects schemes, paths, whitespace, XML metacharacters and
values over 127 bytes rather than letting the native client truncate them.
Invalid configuration fails at server startup. The XML shape, validation and
route wiring are covered by `tests/test_native_service_hosts.py` (`TESTED`);
that the redirect is honoured during a real game launch remains `UNVERIFIED`.

### Deployment host check (`TESTED`)

The unpatched launcher's compiled `SERVER_HOST` is
`roscaelifer.thevakhovske.eu.org`. Probing that host with an invalid token
returned Express 404 (`Cannot POST ...`) for `/auth.asmx/CreateTicketScAuthToken`,
`/gta5/11/gameservices/auth.asmx/CreateTicketScAuthToken` and
`/gwsc/api/events`, with `server: Caddy` / `x-powered-by: Express`. It does not
proxy this FastAPI backend.

The patched launcher v2 bundled in `assets/gwsc.properties` sets
`SERVER_HOST=127.0.0.1:8443`, `WEB_URL=https://127.0.0.1:8443/` and
`SELF_SIGNED_CERT=1` (`SOURCE_CONFIRMED`). A temporary local backend on that
origin answered `/health` and `/gwsc/api/game-launch` (`TESTED`), but the game
still showed the Online login prompt on the user's attempt. That symptom is
`UNVERIFIED` at the protocol level: no native request log or redacted FastAPI
access log was captured. The next diagnostic is to confirm whether any native
`.asmx` request reaches this backend after the handoff. `GTAVNativeActivity` is
not exported, so an external direct launch is denied by Android.

The ticket and session ticket are random private values, stored only as SHA-256
hashes in `service_tickets` and bound to the authenticated account, service,
title and platform. They use `GWSC_TICKET_EXPIRY` (default 3600 s). Each service
keeps at most `GWSC_MAX_TICKETS_PER_ACCOUNT` tickets; the `game-launch` and SC
token services have their own caps, so a burst of launches cannot evict an
in-use game credential.

### Legacy `CreateTicket` aliases

```text
/{title}/{version}/{service}/CreateTicket
/{title}/{version}/CreateTicket
/CreateTicket
```

These now emit the same parser-valid credential XML as the `.asmx` methods.
The service value continues to come from the authenticated route/request and
is stored only as ticket metadata.

### `POST /ros/ticket/renew`

Aliases: `/ros.ticket.renew`, `/gwsc/api/ros/ticket/renew`.

Requires the account token and the existing ticket. The old ticket is revoked
and a new ticket is issued with the same service/title/platform. Another
account cannot renew someone else's ticket (404). Renewed tickets also carry
the full parser-valid credential XML.

### `POST /ros/ticket/invalidate`

Aliases: `/ros.ticket.invalidate`, `/gwsc/api/ros/ticket/invalidate`.

Requires the account token and ticket. Sets `revoked_at`; revoked tickets
cannot be renewed. Another account cannot invalidate someone else's ticket.

## SC auth token response (`SOURCE_CONFIRMED`, `TESTED`)

`rlScCreateScAuthTokenTask::ProcessSuccess` reads the text of the `<Result>`
element. LSO also repeats the token in a `ScAuthToken` attribute on `Response`;
the server now emits both shapes (`SOURCE_CONFIRMED`, `TESTED`):

```xml
<Response ScAuthToken="gwsc_ticket_...">
  <Status>1</Status>
  <Result>gwsc_ticket_...</Result>
</Response>
```

It resolves through the same native authentication fallback as launch tickets,
but is not accepted by launcher REST sessions.

## Native matchmaking (`SOURCE_CONFIRMED` LSO shape, `TESTED`)

The following literal methods are registered with the same prefixed and
unprefixed forms as other native services:

```text
/{title}/{version}/gameservices/matchmaking.asmx/Advertise
/{title}/{version}/gameservices/matchmaking.asmx/Update
/{title}/{version}/gameservices/matchmaking.asmx/Find
/{title}/{version}/gameservices/matchmaking.asmx/Unadvertise
/{title}/{version}/gameservices/matchmaking.asmx/UnadvertiseAll
```

Requests are authenticated with `ticket`, `scAuthToken`, `SessionTicket` or the
observed header variants. `attrsJson` must be a JSON object; its `Data` member
is the client's opaque base64 peer record. The server stores that string
verbatim in `game_sessions.advertised_data` and returns it unchanged from
`Find` as `<R MatchId="..." Owner="..."><Data>...</Data></R>` inside a
`DynamicResponse` envelope. It never fabricates a `netPeerAddress` record.
`AvailableSlots`, `numSlots`, platform and expiry are validated and stored so
filtering is real rather than a catch-all success handler.

Advertisements expire after `GWSC_NATIVE_MATCHMAKING_SESSION_SECONDS` (default
8 hours, following LSO). `Unadvertise` is host-only and idempotent for unknown
match IDs. `tests/test_native_matchmaking.py` covers advertise/Find echo,
Update, Unadvertise, UnadvertiseAll, bad JSON and unauthenticated calls
(`TESTED`). Whether the installed Android client accepts the echoed `Data`
remains `UNVERIFIED`.

## P2P certificates (`SOURCE_CONFIRMED` LSO shape, `TESTED`)

`POST /{title}/{version}/{serviceSet}/auth.asmx/CreateP2PCertificate` accepts
authenticated `pubKey` and `sig` fields. `pubKey` must be base64 for a 65-byte
uncompressed P-256 point; `sig` is required, bounded and base64-valid but not
cryptographically verified because the Android meaning is `UNVERIFIED`.

`gwsc_local_termux/native_crypto.py` generates a static local P2P CA on first
use and issues a client certificate with OpenSSL `-force_pubkey`, so the issued
certificate contains the client's key, not a server-generated one. Private
keys are written with mode `0600` and never logged. The response contains the
client certificate followed by the CA certificate in `CertChain`, and
`SecsUntilExpiration` from `GWSC_P2P_CERT_TTL` (default 86400).

Paths can be overridden with `GWSC_P2P_CA_KEY`, `GWSC_P2P_CA_CERT` and
`GWSC_P2P_CERT_DIR`. Rotating the CA requires a coordinated client update;
partial key/cert pairs fail closed. `tests/test_native_crypto.py` and
`tests/test_native_p2p_relay.py` verify the CA signature, client public key and
response shape (`TESTED`).

## Relay discovery (`SOURCE_CONFIRMED` LSO shape, `TESTED`)

`POST /{title}/{version}/{serviceSet}/GeoLocation.asmx/GetRelayServers` (and
the `GetLocationInfoFromIP` alias) requires native authentication and a
configured public UDP relay:

```sh
GWSC_RELAY_PUBLIC_ADDRESS=your.public.host
GWSC_RELAY_PUBLIC_PORT=6672
```

The endpoint returns LSO's `RegionBucketLookUpResponse` shape with a P-256 SPKI
public key on `RelaysList` and a raw `r||s` ECDSA-SHA256 signature over the
literal `host:port` string on `Server/@Sig`. The relay signing key is generated
once under `GWSC_RELAY_CERT_DIR`, stored with mode `0600`, and is independent of
the TLS certificate and P2P CA. Unconfigured public address or missing OpenSSL
returns XML errors rather than an unsigned relay list. Signature verification
is covered by `tests/test_native_crypto.py` and the endpoint by
`tests/test_native_p2p_relay.py` (`TESTED`). Android acceptance is
`UNVERIFIED`.

`POST /{title}/{version}/{serviceSet}/Presence.asmx/GetPresenceServers` returns
LSO's `GetPresenceServersResponse` shape and reuses the same configured public
relay host. It is authenticated and returns 503 when no public relay is
configured (`TESTED`).

## Presence

### `CommitPresence`

Pattern: `/{prefix}CommitPresence` (also `/CommitPresence`).

Fields: `state` (`offline`, `launcher`, `in_game`, `in_session`, `away`),
optional `sessionId`.

Presence is stored in `presence_records` with an expiry
(`GWSC_PRESENCE_TIMEOUT`, default 300 s). A stale record is removed by the
maintenance worker and `/gwsc/api/info` then reports the real online count.

### `Presence`

Pattern: `/{prefix}Presence` (also `/Presence`).

Returns the caller's own presence state, online flag and expiry.

## Static settings

`GET|POST /RosGamersSettings.json` returns a placeholder settings document.
It is not a claimed Rockstar contract.

## Removed behaviour

The original prototype had a catch-all native route that returned generic
success XML. It has been removed. Unknown native paths return 404.
