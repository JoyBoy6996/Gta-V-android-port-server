# LSO → FastAPI parity matrix

`SOURCE_CONFIRMED` means the route and shape were read from the upstream
Los Santos Online source at
`/data/user/0/com.termux/files/usr/tmp/opencode/lso-upstream`.
`TESTED` means the FastAPI implementation has automated coverage.
`PLANNED` means the route is inventoried but not implemented in this repository
yet. `INTENTIONALLY DISABLED` means the route is inventoried and deliberately
left unimplemented because its upstream contract cannot be made safe without a
new authorization/proof design. `ADAPTED` means the upstream behavior is
implemented with our stricter credential, validation or storage rules.

This document is the work plan for the user-selected **Full LSO parity** scope.
It does not claim that platform-specific crypto, object storage, or Discord
features are active without configuration.

## Implementation policy

Every ported route keeps these FastAPI rules:

- authenticated via `auth.authenticate_native_request()` only — no implicit
  “latest online user” and no unauthenticated catch-all success;
- credentials accepted through the same form/header names as existing native
  routes (`ticket`, `scAuthToken`, `sessionTicket`, `ros-SessionTicket`,
  `Authorization: Bearer`, `X-GWSC-Token`);
- bounded input parsing via `native_requests.request_values()`;
- XML built with `xml.etree.ElementTree` through `xml_protocol.py`, never by
  string concatenation with client-controlled values;
- HTTP status plus XML `Status=0`/`Error` on failures;
- no secrets, password bodies, session keys, tickets, or raw captures in logs;
- opaque LSO `attrsJson.Data` stored and echoed verbatim, never synthesized.

`INTENTIONALLY DISABLED` routes: the user chose to keep the platform-auth routes
(`CreateTicketNp3/Np4/Np5`, `CreateTicketXbl2`, `ImpersonateCreateTicketXbl`,
`loginPS4`, `UpdateTicketSc3`) and `CreateP2PCertificateCyprus` disabled.
`SOURCE_CONFIRMED`: upstream trusts client-supplied gamertags/platform
identifiers and session keys, and some paths disable TLS verification. Porting
those contracts as-is would let a caller select an arbitrary account or mint a
credential for it. These routes stay unimplemented/inert unless a new explicit
proof model is authorized and reviewed; this is a deliberate security decision,
not an outstanding implementation task. The normal Android launcher ticket,
service-ticket and session-ticket paths remain `ADAPTED` + `TESTED`.

Upstream LSO uses platform-specific RC4 request/response encryption and
PS3/PS4/Xbox platform keys for most native routes. Android static analysis so
far confirms AES for ticket keys and XOR+base64 only for the ROS User-Agent;
no Android ROS RC4 path has been found (`INFERRED`). The FastAPI port therefore
accepts plaintext native form/JSON/XML at the same paths and applies strict
account-bound credentials.

## Status summary

| Family | LSO routes | FastAPI status |
| --- | --- | --- |
| health / static | ~40 | Static assets `ADAPTED` + `TESTED`; `/health`, `/healthz` `TESTED` |
| credentials / auth | 10 | `ADAPTED` for Android ticket paths; platform auth `INTENTIONALLY DISABLED` |
| matchmaking | 5 | `ADAPTED` + `TESTED` |
| P2P / relay / presence servers | 4 | `ADAPTED` + `TESTED` |
| stateless `.asmx` services | ~18 | Tranche 1 + `TESTED` |
| social/profile/presence state | ~18 | Presence attributes/messages `ADAPTED` + `TESTED`; profile stats `ADAPTED` + `TESTED` |
| saves / profile stats / UGC / news | ~25 | Saves, profile stats, UGC binary/lifecycle and news `ADAPTED` + `TESTED` |
| clans / leaderboards / inbox | ~17 | Clans/leaderboards `ADAPTED` + `TESTED`; inbox static `ADAPTED` + `TESTED` |
| cloud binaries / static assets | ~30 | Cloud persistence and static assets `ADAPTED` + `TESTED` |
| Discord bot | external process | FastAPI groundwork `TESTED`; bot process remains external |

## Global and cloud routes

| LSO method + path | Handler / storage | FastAPI status | Notes |
| --- | --- | --- | --- |
| `GET /healthz` | inline | `ADAPTED` + `TESTED` | Exact alias of `/health` |
| `POST /gta5/11/gameservices/Complaint.asmx/WriteComplaint` | inline stub | Tranche 1 | Static status response with valid XML |
| `POST /cloud/11/cloudservices/members/{xbl\|sc\|np}/:id/GTA5/saves/mpstats` | `saves/savefiles.js`, S3 | `ADAPTED` + `TESTED` | Multipart save write; account from authenticated credential, safe filename |
| `GET /cloud/11/cloudservices/members/{xbl\|sc\|np}/:id/GTA5/saves/mpstats/:name` | `saves/savefiles.js`, S3 | `ADAPTED` + `TESTED` | Binary save read; account-scoped key; `DoesNotExist` XML on miss |
| `POST /gta5/11/gameservices/auth.asmx/CreateP2PCertificateCyprus` | `p2pCertificates/createP2PCertificate.js` | `INTENTIONALLY DISABLED` | JSON identity fields are untrusted; no account-binding proof design |
| `POST /gta5/11/gameservices/auth.asmx/CreateP2PCertificate` | `p2pCertificates/createP2PCertificate.js` | `ADAPTED` + `TESTED` | Existing `native_crypto` CA issuance, stricter signature validation |
| `GET /cloud/11/cloudservices/ugc/gta5mission/:hash/:file` | `ugc/getMissionFile.js`, UGC S3 | `ADAPTED` + `TESTED` | Mission/photo binary with primary/fallback/default lookup |
| `GET /cloud/11/cloudservices/ugc/gta5photo/:hash/:file` | `ugc/getMissionFile.js`, UGC S3 | `ADAPTED` + `TESTED` | Same handler; unpublished content is owner-scoped |
| `POST /cloud/11/cloudservices/ugc/gta5photo/upload/:contentId/:file` | `ugc/photoUpload.js`, UGC S3 | `ADAPTED` + `TESTED` | Route-specific HMAC ACL, 900s expiry, 25MB cap, no debug dumps |
| `POST /ugc/gta5photo/:contentId/:file` | `ugc/photoUpload.js` | `ADAPTED` + `TESTED` | Alias with its own ACL |
| `POST /cloud/11/cloudservices/members/np/:scId/share/gta5/mpchars` | inline, S3 | `ADAPTED` + `TESTED` | Path id validated; storage scoped to authenticated account |
| `GET /cloud/11/cloudservices/titles/gta5/ps4/news/news.json` | `news/getNewsItems.js` | `ADAPTED` + `TESTED` | Real active/date/expire filtering from `news` |
| `GET /cloud/11/cloudservices/global/sc/news/:storyKey/:langFile` | `news/getNewsItems.js` | `ADAPTED` + `TESTED` | Source-confirmed story shape; `langFile` compatibility no-op |
| `GET /cloud/11/cloudservices/global/news/image/:newsId/:image` | `news/getNewsImage.js` | `ADAPTED` + `TESTED` | News bucket profile; safe key; explicit 404 text |
| `GET /cloud/11/cloudservices/crews/sc/:clanId/publish/emblem/emblem_:size.dds` | `clans/getCrewEmblem.js` | `ADAPTED` + `TESTED` | Authenticated member/open-clan read; safe key prefix, size cap; real JPEG/PNG bytes; Pillow resize when installed |
| `GET /cloud/11/cloudservices/titles/gta5/ps4/0x1a098062.json` | static tunables | `ADAPTED` + `TESTED` | Byte-exact 22,151 B blob with digest verification |
| `GET /cloud/11/cloudservices/titles/gta5/pcros/0x1a098062.json` | redirect | `ADAPTED` + `TESTED` | 302 only to allowlisted HTTPS target; unconfigured returns 404 |
| `GET /titles/gta5/ps2/0x1a098062.json` | static JSON | `ADAPTED` + `TESTED` | One handler instead of upstream duplicate registrations |
| `GET /cloud/11/cloudservices/titles/gta5/xboxone/0x1a098062.json` | redirect via `ROS_HOST` | `ADAPTED` + `TESTED` | Local relative 302 to the PS2 blob; no host injection |
| `GET /cloud/11/cloudservices/titles/gta5/{ps4,xboxone}/bgscripts/bg_902_0.rpf` | redirects | `ADAPTED` + `TESTED` | Exact configured 302; duplicate upstream registration dropped |
| `GET /cloud/11/cloudservices/titles/gta5/{ps4,xboxone}/extraContent/ExtraContentManifest.xml` | redirects | `ADAPTED` + `TESTED` | Allowlisted HTTPS targets only |
| `GET /cloud/11/cloudservices/titles/gta5/{ps4,xboxone}/check.json` | redirects | `ADAPTED` + `TESTED` | Allowlisted HTTPS targets only |
| `GET /cloud/11/cloudservices/titles/gta5/xbox360/news/news.json` | static news | `ADAPTED` + `TESTED` | Byte-exact 255 B static list |
| `GET /cloud/11/cloudservices/titles/gta5/xbox360/0x1a098062.json` | redirect | `ADAPTED` + `TESTED` | Configured HTTPS tunables target |
| `GET /titles/gta5/ps3/0x1a098062.json` | `tunables/tunables.js` | `DELIBERATELY OMITTED` | Stable 501: upstream AES key/state mutation is not copied; no captured contract |
| `GET /cloud/11/cloudservices/titles/gta5/xbox360/bgscripts/bg_900_0.rpf` | redirect | `ADAPTED` + `TESTED` | Configured HTTPS tunables target |
| `GET /cloud/11/cloudservices/titles/gta5/ps5/bgscripts/bg_ng_171_8.rpf` | static file | `ADAPTED` + `TESTED` | Correct `application/octet-stream` instead of upstream's JSON label |
| `GET /cloud/11/cloudservices/titles/gta5/ps5/0x1a098062.json` | static file | `ADAPTED` + `TESTED` | Byte-exact 33,427 B blob |
| `GET /cloud/11/cloudservices/titles/gta5/ps5/legal/version/version_num.xml` | static file | `ADAPTED` + `TESTED` | Uses the source-confirmed PS4 file; explicit XML type |
| `POST /gta5/11/gameservices/entitlements.asmx/GetSubscriptionDataPSN` | inline stub | Tranche 1 | Static active subscription XML |
| `GET /titles/gta5/xbox360/bgscripts/bg_900_0.rpf` | static file | `ADAPTED` + `TESTED` | Byte-exact 8,192 B RPF with digest verification |
| `GET /titles/gta5/xbox360/extraContent/ExtraContentManifest.xml` | static file | `ADAPTED` + `TESTED` | Byte-exact 761 B manifest; explicit XML type |
| `POST /gta5/11/gameservices/AgeAssurance.asmx/PlayerStatus` | inline stub | Tranche 1 | `NotRequired` |
| `GET /cloud/11/cloudservices/titles/gta5/xbox360/check.json` | redirect | `ADAPTED` + `TESTED` | Configured HTTPS tunables target |
| `GET /titles/gta5/xbox360/check.json` | static file | `ADAPTED` + `TESTED` | Byte-exact 2 B JSON |
| `GET /cloud/11/cloudservices/titles/gta5/xbox360/store/en/commerceData.xml` | redirect | `ADAPTED` + `TESTED` | Configured HTTPS tunables target |
| `GET /titles/gta5/{ps4,xbox360}/store/.../commerceData.xml` | static file | `ADAPTED` + `TESTED` | Byte-exact files; explicit XML type; no `application/json` mislabelling |
| `GET /cloud/11/cloudservices/titles/gta5/{ps4,xbox360}/store/images/editions/:image` | redirect | `ADAPTED` + `TESTED` | Strict image-name validation; allowlisted HTTPS target only |
| `GET /cloud/11/cloudservices/titles/gta5/ps4/legal/version/version_num.xml` | static file | `ADAPTED` + `TESTED` | Same source-confirmed PS4 file as the PS5 path |
| `GET /cloud/11/cloudservices/members/sc/1/GTA5/{dog,car}/appnpps4.json` | inline JSON | `ADAPTED` + `TESTED` | Exact source-confirmed placeholder payloads; no generated state |

## `.asmx` routers

| LSO method + path | Handler / storage | FastAPI status | Notes |
| --- | --- | --- | --- |
| `POST achievements.asmx/Synchronize` | inline stub | Tranche 1 | Status-only XML |
| `POST achievements.asmx/GetPlayerAchievements` | hardcoded list | Tranche 1 | Private achievements table later |
| `POST auth.asmx/CreateTicketNp3\|Np4\|Np5` | BlueSphere | `INTENTIONALLY DISABLED` | Platform proof required; do not port TLS bypass |
| `POST auth.asmx/CreateTicketXbl2` | XBL identity | `INTENTIONALLY DISABLED` | Trusts gamertag upstream; no account-binding proof |
| `POST auth.asmx/ImpersonateCreateTicketXbl` | XBL impersonation | `INTENTIONALLY DISABLED` | High-risk impersonation; no authorization design |
| `POST /loginPS4` | PS4 login code | `INTENTIONALLY DISABLED` | Non-RC4 JSON; platform credential proof unavailable |
| `POST /UpdateTicketSc3` | PC credentials | `INTENTIONALLY DISABLED` | Non-RC4; odd clan XML response; no proof design |
| `POST socialclub.asmx/GetPasswordRequirements` | static | Tranche 1 | Policy matches our validators |
| `POST socialclub.asmx/CheckText` | static | Tranche 1 | `Count=0` |
| `POST socialclub.asmx/CreateScAuthToken` | session ticket | `ADAPTED` + `TESTED` | Hashed account-bound token |
| `POST auth.asmx/CreateTicketSc3` etc. | PC credentials | `ADAPTED` + `TESTED` | Android paths; no plaintext password transport |
| `POST CashTransactions.asmx/GetPackValueUSDE` | static catalog | Tranche 1 | 29 public packs |
| `POST CashTransactions.asmx/GetLegalTerritoryRestrictions` | static | Tranche 1 | Casino restrictions |
| `POST Clans.asmx/GetInvites` | `ClanInvite` DB | `ADAPTED` + `TESTED` | Real account-scoped invites; corrected XML escaping |
| `POST Clans.asmx/SetPrimaryClan` | `ClanMembership` DB | `ADAPTED` + `TESTED` | Membership check required; strict positive `clanId` |
| `POST Clans.asmx/GetPrimaryClans` | inline | `ADAPTED` + `TESTED` | Derived from `users.primary_clan_id`, not hardcoded |
| `POST Clans.asmx/GetMetadataForClan` | inline | `ADAPTED` + `TESTED` | Static metadata with upstream `ms=15` |
| `POST Clans.asmx/GetMine` | `ClanMembership` DB | `ADAPTED` + `TESTED` | Real member counts and primary flag |
| `POST Clans.asmx/GetDesc` | `Clan` DB | `ADAPTED` + `TESTED` | Real `IsSystemClan` value; empty list when absent |
| `POST Leaderboards.asmx/ReadByCrews` | inline empty result | `ADAPTED` + `TESTED` | Bare XML root with lowercase `count`/`total` |
| `POST GeoLocation.asmx/GetRelayServers` | relay keys | `ADAPTED` + `TESTED` | Signed host |
| `POST GeoLocation.asmx/GetLocationInfoFromIP` | same handler | `ADAPTED` + `TESTED` | Alias |
| `POST Inbox.asmx/GetUnreadMessages` | static | Tranche 1 | Empty inbox |
| `POST Inbox.asmx/PostMessageToRecipients` | no-op | Tranche 1 | Validated status response |
| `POST legalpolicies.asmx/GetAcceptedVersion` | static | Tranche 1 | `Version=7`, `PolicyTag=pp` |
| `POST licenseplates.asmx/Get` | static | Tranche 1 | `JORBY` plate |
| `POST licenseplates.asmx/IsValid` | static | Tranche 1 | Always valid upstream |
| `POST Presence.asmx/GetPresenceServers` | relay config | `ADAPTED` + `TESTED` | Host-only, no signature |
| `POST Presence.asmx/Publish` | no-op | Tranche 1 | Validated status response |
| `POST Presence.asmx/MultiPostMessage` | `social_messages` DB | `ADAPTED` + `TESTED` | Authenticated sender; quoted recipients resolved to accounts |
| `POST Presence.asmx/PostMessageToRecipients` | no-op | Tranche 1 | Validated status response |
| `POST Presence.asmx/GetAttributes` | `presence_attributes` DB | `ADAPTED` + `TESTED` | Exact `GetAttributes` CSV shape; account-bound handle lookup |
| `POST Presence.asmx/GetAttributesForGamers` | no-op | Tranche 1 | Validated status response |
| `POST Presence.asmx/Subscribe\|Unsubscribe\|SignOut` | no-op | Tranche 1 | Validated status responses |
| `POST Presence.asmx/GetMessages` | `social_messages` DB | `ADAPTED` + `TESTED` | Real count/timestamp; marks delivered in one transaction |
| `POST Presence.asmx/Query\|QueryWithMaxRecordLength` | attributes/active player | `ADAPTED` + `TESTED` | One canonical `PresenceQueryResponse`; 5-minute cutoff, cap 10 |
| `POST Presence.asmx/SetAttributes\|ReplaceAttributes` | `presence_attributes` DB | `ADAPTED` + `TESTED` | Literal comma-split triples; reject non-triples; account-scoped |
| `POST ProfileStats.asmx/ReadStatsByGamer2` | account stat blob + `stat_lookups` | `ADAPTED` + `TESTED` | Type+value-only payloads; only self handles accepted; no zero-fill |
| `POST ProfileStats.asmx/ReadStatsByGroups` | account stat blob + lookup | `ADAPTED` + `TESTED` | Signed BE group ids; PS5 uses `gen9_stat_lookups`; only stored values returned |
| `POST ProfileStats.asmx/WriteStats` | account stat blob + lookup | `ADAPTED` + `TESTED` | Bounded raw-DEFLATE parse; seeded allowlist; skips server-authoritative/fabricated-time ids |
| `POST ProfileStats.asmx/ResetStats` | static | `ADAPTED` + `TESTED` | Exact source-confirmed XML; lowercase `count` attribute |
| `POST SaveMigration.asmx/GetSourceAccountsMP` | static | `ADAPTED` + `TESTED` | Source-confirmed empty-accounts XML; authenticated |
| `POST SaveMigrationSingleplayer.asmx/GetUploadedSingleplayerSaveMetadata` | authenticated account | `ADAPTED` + `TESTED` | Same element order; empty token instead of raw `SessionKey`; no fabricated times |
| `POST telemetry.asmx/GetTelemetryClientConfig` | static | Tranche 1 | Config attributes on root |
| `POST telemetry.asmx/SubmitCompressed` | compressed log | Tranche 1 | Validate/decompress bounded payload, no log body |
| `POST ugc.asmx/CheckText` | static | Tranche 1 | Shared with Social Club |
| `POST ugc.asmx/CreateContent\|UpdateContent` | `ugc_contents` + UGC object store | `ADAPTED` + `TESTED` | Source-confirmed `&data=` big-endian frames; server-generated base64url ids; strict bounds; owner-only updates |
| `POST ugc.asmx/Publish\|SetDeleted` | `ugc_contents` | `ADAPTED` + `TESTED` | Publish unpublishes siblings; SetDeleted is a soft unpublish with owner check (upstream had none) |
| `POST ugc.asmx/QueryContent` | `ugc_contents`, bookmarks, static heists | `ADAPTED` + `TESTED` | Bounded JSON5-like params; published-or-owner reads; byte-exact heist XML for the two source-confirmed triggers; unknown query returns `Status=0` |

## Existing native routes already adapted

These were implemented before the parity port and remain part of the matrix:

- `auth.asmx/CreateTicketScAuthToken`, `CreateTicketSc3`, `CreateTicket`,
  `socialclub.asmx/CreateTicketScAuthToken`, `CreateScAuthToken`;
- `matchmaking.asmx/Advertise`, `Update`, `Find`, `Unadvertise`,
  `UnadvertiseAll`, plus legacy `Matchmaking*Task` paths;
- `auth.asmx/CreateP2PCertificate`;
- `GeoLocation.asmx/GetRelayServers`, `GetLocationInfoFromIP`;
- `Presence.asmx/GetPresenceServers`;
- `/ros/ticket/renew`, `/ros/ticket/invalidate`.

## Schema v9 tables

`TESTED`: schema v9 is implemented in `database.py`. It includes the v8 tables:

- `news`, `social_messages` (presence-delivery style), `ugc_contents`, `ugc_mission_bookmarks`,
  `ugc_mission_playlists`, `ugc_mission_playlist_items`;
- `tunables`, `stat_lookups`, `gen9_stat_lookups`;
- `clans`, `clan_ranks`, `clan_memberships`, `clan_invites`, `clan_join_requests`,
  `clan_wall_messages`;
- `user_ip_addresses`, `launch_verifications`;
- per-account ordered `presence_attributes` (`value_type`, `value_text`,
  `sort_order`) and `users.primary_clan_id` for primary-clan state.

`TESTED`: v9 adds `UNIQUE (rockstar_id, platform)` to `active_players` and a
`(platform, last_seen)` index. The v8→v9 migration rebuilds the table in place,
keeps the newest `MAX(last_seen)` observation per account/platform pair, and is
idempotent. `database.touch_active_player()` only writes allowlisted platforms
(`PS4`, `PS5`, `XBOX360`, `XBOXONE`, `PCROS`) and positive account ids.

`TESTED`: `native_requests.require_user()` is the single writer for native
observations. After successful authentication it upserts the caller using the
validated `Platform` field/header. Missing or unknown platforms are ignored, and
a bookkeeping failure never changes the authentication decision. Authenticated
`Presence.asmx/Query` refreshes its caller's row before reading, matching the
source-confirmed upstream order.

`TESTED`: `database.active_player_counts()` returns real per-platform counts
within a configurable window (default 600 seconds) and a total. Unknown
platforms are excluded. No fabricated or cached online count is stored.

`TESTED`: the local/S3-compatible adapter in `object_storage.py` validates every
key component, scopes saves and shares to the authenticated internal account id,
and keeps separate bucket profiles for UGC, news and emblems. S3 credentials and
the photo-upload HMAC secret are environment-only. No production bucket names,
credentials, or hardcoded encryption keys were copied into the repository.

`ADAPTED` + `TESTED`: static assets are vendored byte-exact under
`gwsc_local_termux/static/` with SHA-256 and size verification in
`static_assets.py`. Opaque tunables/RPF bytes are never parsed, decrypted or
logged. Static redirects require an HTTPS base URL and a hostname in the
configured allowlist; unconfigured targets return 404 and misconfigured targets
return 500 without echoing configuration. Crew emblems require an authenticated
member or an open clan, a restricted `emblems/` key prefix, a size cap and
JPEG/PNG magic checks. Pillow resizes when installed; otherwise verified source
bytes are served unchanged.

`ADAPTED` + `TESTED`: `routes/lso_save_migration.py` implements the two
SaveMigration stubs with the source-confirmed bare method namespaces and
`Status`-last element order. `GetSourceAccountsMP` returns the constant empty
`<Accounts>` result. The singleplayer handler keeps the real authenticated
rockstar id but emits an empty `SaveMigrationRecordToken`: upstream's value is
the raw RC4 session key and is never reproduced. Its two timestamps are neutral
zero sentinels, not request-time values dressed up as a real save upload.
Neither handler reads a body, S3 object or other account's data.

`ADAPTED` + `TESTED`: `routes/lso_ugc.py` implements the five UGC lifecycle
methods. The source-confirmed `&data=` frame decoder is bounded by file count,
file size and total body size; trailing garbage is rejected. Ids are 16 random
bytes in base64url form and are generated server-side. Ownership comes only from
the authenticated account: Update/Publish/SetDeleted reject non-owners, and
public QueryContent filters hide unpublished rows. Publish unpublishes sibling
versions of the same root id; SetDeleted performs the source-confirmed soft
unpublish instead of upstream's unverified blanket success response. Photo
create metadata uses a configured HTTPS base URL plus a short-lived HMAC upload
token and never trusts the request `Host` header. The two source-confirmed heist
trigger ids return byte-exact digest-pinned XML from `gwsc_local_termux/static/`.

`ADAPTED` + `TESTED`: `routes/lso_stats.py` implements all four ProfileStats
methods with the source-confirmed XML namespaces and binary layouts. Stat
definitions come only from the opt-in `stat_seeds.py` importer, which decodes the
two upstream metadata snapshots locally, normalises the 65 hex-prefixed legacy
hashes correctly, preserves all 24 fields and verifies the documented counts and
Gen9-subset relationship before an atomic table replacement. `OBSERVED`: the
importer was exercised read-only against the real upstream snapshots and
matched 40,849/28,401 rows, the Gen9 subset, and all 65 hex-prefixed legacy
hashes. The codec uses synthetic-vector tests only; no player blob is vendored. Reads return only real
stored values for the authenticated account's validated platform: no zero-fill,
no current-time overrides, and no arbitrary gamer handles. Writes are bounded,
accept only seeded ids whose type matches, skip server-authoritative and the
three upstream fabricated-time ids, and merge under a lock. `ResetStats` is
pinned to the upstream lowercase `count` attribute.

`UNVERIFIED`: whether the Android client accepts an empty `Values`/`Results`
envelope before any stats have been written, and the semantics of the discarded
first byte of the compressed `WriteStats` payload. Exact upstream RC4 response
framing is not ported; stat definitions must be imported explicitly by an
operator before group/hash resolution has any data.

`TESTED`: Discord support is groundwork only. FastAPI does not read a Discord
token or publish a Discord endpoint; it exposes inert, non-secret configuration
placeholders, the real `active_player_counts()` data source, and log redaction
for Discord credentials. The upstream bot remains a separate external process.

`UNVERIFIED`: the Android client has not yet been observed calling these cloud,
static, emblem, profile-stats or native active-player paths, and no successful Android Discord
integration has been captured. Upstream RC4 response framing is deliberately not
ported; native authentication uses the existing ticket/session-ticket plumbing.
