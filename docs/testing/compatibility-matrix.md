# Compatibility Matrix

This matrix is deliberately conservative. **Automated tests prove internal
behaviour, not compatibility with the real Android client.** No successful
game session has been captured.

## Evidence labels

| Label | Meaning |
| --- | --- |
| `OBSERVED` | Captured from the actual Android game or verified in client code. |
| `SOURCE_CONFIRMED` | Found in static inspection/symbols, not observed in traffic. |
| `INFERRED` | Reasoned hypothesis that still needs verification. |
| `UNVERIFIED` | Implemented placeholder/private contract. Never assume compatible. |
| `TESTED` | Covered by an automated test in `tests/`. |

## Surfaces

| Surface | Implemented | Automated tests | Evidence | Notes |
| --- | --- | --- | --- | --- |
| Login request (`POST /gwsc/api/login`) | Yes | `test_register_login_and_profile` | `OBSERVED` request | Fields `email`, `password`, `client`. |
| Login success response `{token, profile}` | Yes | `test_register_login_and_profile` | `UNVERIFIED` | Field names/nesting not confirmed by client. |
| Login error response | Yes | `test_invalid_login_is_generic` | `UNVERIFIED` | Stable `{error, message, detail}`; client parser unknown. |
| Register | Yes | `test_register_login_and_profile`, `test_invalid_registration_fields`, `test_duplicate_email_and_nickname_rejected` | `UNVERIFIED` | Private contract. |
| Logout / logout-all | Yes | `test_revoked_token_is_rejected`, `test_logout_all_revokes_every_session` | `UNVERIFIED` | Token revocation is server-side and tested. |
| Profile `/me`, `PATCH /me` | Yes | `test_register_login_and_profile` | `UNVERIFIED` | Email only on `/me`. |
| Password change | Yes | `test_password_change_requires_current_and_revokes_other_sessions` | `UNVERIFIED` | Requires current password; revokes other sessions. |
| Avatar presets | Yes | — | `UNVERIFIED` | Validated against a fixed list. |
| Public profile + blocking | Yes | `test_blocking_hides_profile_and_prevents_messages` | `UNVERIFIED` | Blocked requester gets 404. |
| Friends | Yes | `test_friend_request_accept_flow`, `test_cannot_accept_without_incoming_request`, `test_cannot_friend_yourself` | `UNVERIFIED` | Pending/accepted/rejected/blocked. |
| Conversations | Yes | `test_conversation_membership_is_enforced`, `test_group_member_removal_authorization` | `UNVERIFIED` | Direct + group; membership enforced. |
| Messages | Yes | `test_message_validation_and_rate_limit` | `UNVERIFIED` | Validation, pagination, rate limit. |
| SSE events | Yes | `test_sse_requires_authentication`, `test_event_bus_never_crosses_accounts` | `SOURCE_CONFIRMED` (SSE suspected) | Per-account only; event schema `UNVERIFIED`. |
| Launcher game launch (`POST /gwsc/api/game-launch`) | Yes | `test_game_launch_returns_ticket_and_stores_only_its_hash`, `test_game_launch_validates_body`, `test_game_launch_requires_authentication` | `SOURCE_CONFIRMED` (installed client) | Request `{buildReport}`, response `{ticket}` matching `[A-Za-z0-9_-]{43}`. Ticket hashed at rest. |
| Launch ticket as native `scAuthToken` | Yes | `test_launch_ticket_authenticates_native_requests_only`, `test_expired_and_revoked_launch_tickets_are_rejected` | `INFERRED` | Native `ScAuthToken` consumption is `UNVERIFIED`; REST sessions reject launch tickets. |
| Shark cards | Yes, disabled by default | — | `UNVERIFIED` | Opt-in only; a request cannot prove purchase. |
| Starter pack | Yes | — | `UNVERIFIED` | Atomic, one-time, ledgered. |
| `CreateTicket` | Yes | `test_create_ticket_requires_authentication`, `test_create_ticket_is_bound_to_authenticated_account` | `UNVERIFIED` | Random private ticket, hashed at rest. |
| Ticket renew/invalidate | Yes | `test_ticket_renew_invalidate_and_ownership` | `UNVERIFIED` | Ownership enforced. |
| `RosGamersSettings.json` | Yes | — | `UNVERIFIED` | Placeholder document. |
| `CommitPresence` / `Presence` | Yes | `test_commit_presence_requires_auth_and_updates_info` | `UNVERIFIED` | Real presence count. |
| Matchmaking advertise/find/join/unadvertise | Yes | `test_matchmaking_advertise_find_join_unadvertise` | `UNVERIFIED` / `INFERRED` | Paths/fields unconfirmed. |
| Unknown native paths | 404 | `test_unknown_native_route_is_not_a_generic_success` | `TESTED` | No catch-all success. |
| UDP relay registration | Yes | `test_relay_registration_requires_session_membership` | `INFERRED` | Private extension; client handshake unknown. |
| UDP relay forwarding | Yes | `test_registry_routes_only_within_session`, `test_unregistered_and_oversized_packets_are_dropped`, `test_registry_expires_stale_peers` | `UNVERIFIED` | Opaque datagrams; no game protocol. |
| Two-device gameplay over relay | No | — | `UNVERIFIED` | **Not tested** across networks/devices. |

## What the tests do and do not prove

**Proven by tests (internal behaviour):**

- Authentication, token expiry/revocation and account status checks.
- Input validation and stable error shapes.
- Authorization: conversation membership, block relationships, session
  ownership, ticket ownership, group member removal.
- Rate limiting and body limits.
- Launch-ticket format, hashing at rest, expiry, revocation and per-service cap.
- SSE isolation between accounts.
- Relay registry isolation, size/rate drops and peer expiry.
- Legacy database migration and token invalidation.
- Capture redaction and request-ID handling.

**Not proven:**

- That the Android client accepts any response shape.
- That the client calls the native paths or fields implemented here.
- That matchmaking advertises/joins the way the client expects.
- That the UDP relay interoperates with the game's transport.
- That the game can complete a login or enter a session.

## Verifying against the client

1. Enable development capture (`GWSC_CAPTURE_ENABLED=true`).
2. Reproduce exactly one action.
3. Inspect the redacted capture.
4. Update the inventory and this matrix with the new evidence label.
5. Add a regression test for any newly confirmed behaviour.

Never promote a surface to `OBSERVED` without a capture or client-code proof.
