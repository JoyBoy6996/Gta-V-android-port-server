# Native ROS / Social Club Services

**Every contract in this document is `UNVERIFIED` unless explicitly marked
otherwise.** The Android client's native request and response formats have
not been captured. The implementation is strict and private; it does not
claim Rockstar compatibility.

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

- form field `scAuthToken`, `authToken` or `token`;
- query parameter `scAuthToken` or `token`;
- `Authorization: Bearer ...`;
- `X-GWSC-Token` header.

There is **no fallback to the latest online user** and no default account.

## Ticket lifecycle

### `CreateTicket`

Supported path patterns (`UNVERIFIED`):

```text
/{title}/{version}/{service}/CreateTicket
/{title}/{version}/CreateTicket
/CreateTicket
```

A ticket is a cryptographically random private value prefixed with
`gwsc_ticket_`. Only its SHA-256 hash is stored in `service_tickets`.
Each ticket is bound to:

- the authenticated account;
- the requested service;
- title/platform metadata;
- an issue time and expiry (`GWSC_TICKET_EXPIRY`, default 3600 s).

At most `GWSC_MAX_TICKETS_PER_ACCOUNT` tickets are retained per account.

Success body:

```xml
<Response>
  <Status>1</Status>
  <Result>
    <Ticket>gwsc_ticket_...</Ticket>
    <SecsUntilExpiration>3600</SecsUntilExpiration>
    <Region>1</Region>
    <PlayerAccountId>10001</PlayerAccountId>
    <Privileges>0</Privileges>
    <DisabledSecurityFlags>0</DisabledSecurityFlags>
  </Result>
</Response>
```

The element names are `INFERRED` from the original prototype and still need
client confirmation.

### `POST /ros/ticket/renew`

Aliases: `/ros.ticket.renew`, `/gwsc/api/ros/ticket/renew`.

Requires the account token and the existing ticket. The old ticket is
revoked and a new ticket is issued with the same service/title/platform.
Another account cannot renew someone else's ticket (404).

### `POST /ros/ticket/invalidate`

Aliases: `/ros.ticket.invalidate`, `/gwsc/api/ros/ticket/invalidate`.

Requires the account token and ticket. Sets `revoked_at`; revoked tickets
cannot be renewed. Another account cannot invalidate someone else's ticket.

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
success XML. It has been removed. Unknown native paths now return 404.
