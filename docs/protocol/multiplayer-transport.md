# Multiplayer Transport and UDP Relay

**Status:** `INFERRED` / `UNVERIFIED`. The Android game's real multiplayer
transport has not been captured. The relay forwards opaque datagrams; it
does not implement the game's gameplay protocol, encryption, handshake or
peer discovery.

## What the relay is not

A UDP relay is not a complete multiplayer server. The game still needs
compatible session creation, player identity, peer discovery, framing,
handshake, NAT behaviour, encryption and synchronization. Do not claim GTA
Online compatibility based on the relay alone.

## Registration

The relay only forwards packets for peers that registered through an
authenticated HTTP call:

```http
POST /gwsc/api/relay/register
Authorization: Bearer <token>
Content-Type: application/json

{ "sessionId": "session-1", "udpPort": 7000 }
```

Rules:

- The account must be the session host or a `joined` member.
- The session must be active.
- The peer is bound to the observed HTTP source address plus the supplied
  UDP port.
- At most `GWSC_RELAY_MAX_PEERS_PER_SESSION` peers are allowed.
- Registration is rate-limited.
- `DELETE /gwsc/api/relay/register` removes the caller's peers.

`GET /gwsc/api/relay/status` returns the caller's peers and registry counts.

## Packet routing

`gwsc_local_termux/multiplayer_relay.py`:

- Looks up the source address in the registry.
- Drops unregistered addresses.
- Drops datagrams larger than `GWSC_RELAY_MAX_DATAGRAM`.
- Applies a per-peer token bucket (`GWSC_RELAY_RATE_PPS`,
  `GWSC_RELAY_RATE_BURST`).
- Forwards only to peers in the same `session_id`.
- Never reflects to arbitrary third-party addresses.
- Expires idle peers after `GWSC_RELAY_PEER_TIMEOUT`.

Counters: `received`, `forwarded`, `dropped_unregistered`, `dropped_size`,
`dropped_rate`, `dropped_send`.

## NAT and connectivity

Unknown until captured. Possible models:

- direct peer-to-peer;
- relay-only;
- STUN-like discovery;
- proprietary handshake;
- a combination.

Test on two devices and two independent networks. A localhost test is not
sufficient.

## Open questions

1. Does the client register its UDP endpoint over HTTP at all?
2. Does the client expect a relay handshake or a specific packet header?
3. Is the relay address discovered through matchmaking or a separate
   service?
4. Is gameplay traffic encrypted/authenticated?
5. What are the real production UDP ports?
6. How does the client handle host migration or disconnect?

## Testing status

- Automated unit tests cover registry isolation, unregistered packets,
  oversized packets, peer expiry and authenticated registration.
- **Not yet verified with two Android devices on separate networks.**
