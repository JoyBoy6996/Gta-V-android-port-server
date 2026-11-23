# LSO → FastAPI parity matrix

`SOURCE_CONFIRMED` means the route and shape were read from the upstream
Los Santos Online source at
`/data/user/0/com.termux/files/usr/tmp/opencode/lso-upstream`.
`TESTED` means the FastAPI implementation has automated coverage.
`PLANNED` means the route is inventoried but not implemented in this repository
yet. `ADAPTED` means the upstream behavior is implemented with our stricter
credential, validation or storage rules.

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

Upstream LSO uses platform-specific RC4 request/response encryption and
PS3/PS4/Xbox platform keys for most native routes. Android static analysis so
far confirms AES for ticket keys and XOR+base64 only for the ROS User-Agent;
no Android ROS RC4 path has been found (`INFERRED`). The FastAPI port therefore
accepts plaintext native form/JSON/XML at the same paths and applies strict
account-bound credentials.

## Status summary

| Family | LSO routes | FastAPI status |
| --- | --- | --- |
| health / static | ~40 | `PLANNED` except `/health` and `/healthz` |
| credentials / auth | 10 | `ADAPTED` for Android ticket paths; platform auth `PLANNED` |
| matchmaking | 5 | `ADAPTED` + `TESTED` |
| P2P / relay / presence servers | 4 | `ADAPTED` + `TESTED` |
| stateless `.asmx` services | ~18 | Tranche 1 + `TESTED` |
| social/profile/presence state | ~18 | Presence attributes/messages `ADAPTED` + `TESTED`; profile stats `PLANNED` |
| saves / profile stats / UGC / news | ~25 | Saves/news/UGC binary `ADAPTED` + `TESTED`; profile stats `PLANNED` |
| clans / leaderboards / inbox | ~17 | Clans/leaderboards `ADAPTED` + `TESTED`; inbox static `ADAPTED` + `TESTED` |
| cloud binaries / static assets | ~30 | Cloud persistence `ADAPTED` + `TESTED`; static assets `PLANNED` |
| Discord bot | external process | `PLANNED` |

## Global and cloud routes

| LSO method + path | Handler / storage | FastAPI status | Notes |
| --- | --- | --- | --- |
| `GET /healthz` | inline | `ADAPTED` | `/health` already exists; add exact alias |
| `POST /gta5/11/gameservices/Complaint.asmx/WriteComplaint` | inline stub | Tranche 1 | Static status response with valid XML |
| `POST /cloud/11/cloudservices/members/{xbl\|sc\|np}/:id/GTA5/saves/mpstats` | `saves/savefiles.js`, S3 | `ADAPTED` + `TESTED` | Multipart save write; account from authenticated credential, safe filename |
| `GET /cloud/11/cloudservices/members/{xbl\|sc\|np}/:id/GTA5/saves/mpstats/:name` | `saves/savefiles.js`, S3 | `ADAPTED` + `TESTED` | Binary save read; account-scoped key; `DoesNotExist` XML on miss |
| `POST /gta5/11/gameservices/auth.asmx/CreateP2PCertificateCyprus` | `p2pCertificates/createP2PCertificate.js` | `PLANNED` | JSON identity fields are untrusted; needs authorization design |
| `POST /gta5/11/gameservices/auth.asmx/CreateP2PCertificate` | `p2pCertificates/createP2PCertificate.js` | `ADAPTED` + `TESTED` | Existing `native_crypto` CA issuance, stricter signature validation |
| `GET /cloud/11/cloudservices/ugc/gta5mission/:hash/:file` | `ugc/getMissionFile.js`, UGC S3 | `ADAPTED` + `TESTED` | Mission/photo binary with primary/fallback/default lookup |
| `GET /cloud/11/cloudservices/ugc/gta5photo/:hash/:file` | `ugc/getMissionFile.js`, UGC S3 | `ADAPTED` + `TESTED` | Same handler; unpublished content is owner-scoped |
| `POST /cloud/11/cloudservices/ugc/gta5photo/upload/:contentId/:file` | `ugc/photoUpload.js`, UGC S3 | `ADAPTED` + `TESTED` | Route-specific HMAC ACL, 900s expiry, 25MB cap, no debug dumps |
| `POST /ugc/gta5photo/:contentId/:file` | `ugc/photoUpload.js` | `ADAPTED` + `TESTED` | Alias with its own ACL |
| `POST /cloud/11/cloudservices/members/np/:scId/share/gta5/mpchars` | inline, S3 | `ADAPTED` + `TESTED` | Path id validated; storage scoped to authenticated account |
| `GET /cloud/11/cloudservices/titles/gta5/ps4/news/news.json` | `news/getNewsItems.js` | `ADAPTED` + `TESTED` | Real active/date/expire filtering from `news` |
| `GET /cloud/11/cloudservices/global/sc/news/:storyKey/:langFile` | `news/getNewsItems.js` | `ADAPTED` + `TESTED` | Source-confirmed story shape; `langFile` compatibility no-op |
| `GET /cloud/11/cloudservices/global/news/image/:newsId/:image` | `news/getNewsImage.js` | `ADAPTED` + `TESTED` | News bucket profile; safe key; explicit 404 text |
| `GET /cloud/11/cloudservices/crews/sc/:clanId/publish/emblem/emblem_:size.dds` | `clans/getCrewEmblem.js` | `PLANNED` | Stored emblem resized as JPEG |
| `GET /cloud/11/cloudservices/titles/gta5/ps4/0x1a098062.json` | static tunables | `PLANNED` | Binary/static adapter |
| `GET /cloud/11/cloudservices/titles/gta5/pcros/0x1a098062.json` | redirect | `PLANNED` | External production redirect |
| `GET /titles/gta5/ps2/0x1a098062.json` | static JSON | `PLANNED` | Registered twice upstream |
| `GET /cloud/11/cloudservices/titles/gta5/xboxone/0x1a098062.json` | redirect via `ROS_HOST` | `PLANNED` | Configurable host only |
| `GET /cloud/11/cloudservices/titles/gta5/{ps4,xboxone}/bgscripts/bg_902_0.rpf` | redirects | `PLANNED` | Duplicate upstream path; first registration wins |
| `GET /cloud/11/cloudservices/titles/gta5/{ps4,xboxone}/extraContent/ExtraContentManifest.xml` | redirects | `PLANNED` | External production redirects |
| `GET /cloud/11/cloudservices/titles/gta5/{ps4,xboxone}/check.json` | redirects | `PLANNED` | External production redirects |
| `GET /cloud/11/cloudservices/titles/gta5/xbox360/news/news.json` | static news | `PLANNED` | Static file adapter |
| `GET /cloud/11/cloudservices/titles/gta5/xbox360/0x1a098062.json` | redirect | `PLANNED` | External tunables redirect |
| `GET /titles/gta5/ps3/0x1a098062.json` | `tunables/tunables.js` | `PLANNED` | Encrypted tunables from DB; do not copy key |
| `GET /cloud/11/cloudservices/titles/gta5/xbox360/bgscripts/bg_900_0.rpf` | redirect | `PLANNED` | External tunables redirect |
| `GET /cloud/11/cloudservices/titles/gta5/ps5/bgscripts/bg_ng_171_8.rpf` | static file | `PLANNED` | Served as `application/json` upstream |
| `GET /cloud/11/cloudservices/titles/gta5/ps5/0x1a098062.json` | static file | `PLANNED` | PS5 tunables binary |
| `GET /cloud/11/cloudservices/titles/gta5/ps5/legal/version/version_num.xml` | static file | `PLANNED` | Reads PS4 file upstream |
| `POST /gta5/11/gameservices/entitlements.asmx/GetSubscriptionDataPSN` | inline stub | Tranche 1 | Static active subscription XML |
| `GET /titles/gta5/xbox360/bgscripts/bg_900_0.rpf` | static file | `PLANNED` | Static adapter |
| `GET /titles/gta5/xbox360/extraContent/ExtraContentManifest.xml` | static file | `PLANNED` | Static adapter |
| `POST /gta5/11/gameservices/AgeAssurance.asmx/PlayerStatus` | inline stub | Tranche 1 | `NotRequired` |
| `GET /cloud/11/cloudservices/titles/gta5/xbox360/check.json` | redirect | `PLANNED` | External tunables redirect |
| `GET /titles/gta5/xbox360/check.json` | static file | `PLANNED` | Static adapter |
| `GET /cloud/11/cloudservices/titles/gta5/xbox360/store/en/commerceData.xml` | redirect | `PLANNED` | External tunables redirect |
| `GET /titles/gta5/{ps4,xbox360}/store/.../commerceData.xml` | static file | `PLANNED` | Static adapter |
| `GET /cloud/11/cloudservices/titles/gta5/{ps4,xbox360}/store/images/editions/:image` | redirect | `PLANNED` | External production redirect |
| `GET /cloud/11/cloudservices/titles/gta5/ps4/legal/version/version_num.xml` | static file | `PLANNED` | Static adapter |
| `GET /cloud/11/cloudservices/members/sc/1/GTA5/{dog,car}/appnpps4.json` | inline JSON | `PLANNED` | Example payloads |

## `.asmx` routers

| LSO method + path | Handler / storage | FastAPI status | Notes |
| --- | --- | --- | --- |
| `POST achievements.asmx/Synchronize` | inline stub | Tranche 1 | Status-only XML |
| `POST achievements.asmx/GetPlayerAchievements` | hardcoded list | Tranche 1 | Private achievements table later |
| `POST auth.asmx/CreateTicketNp3\|Np4\|Np5` | BlueSphere | `PLANNED` | Platform proof required; do not port TLS bypass |
| `POST auth.asmx/CreateTicketXbl2` | XBL identity | `PLANNED` | Trusts gamertag upstream; needs authorization review |
| `POST auth.asmx/ImpersonateCreateTicketXbl` | XBL impersonation | `PLANNED` | High-risk; do not enable blindly |
| `POST /loginPS4` | PS4 login code | `PLANNED` | Non-RC4 JSON |
| `POST /UpdateTicketSc3` | PC credentials | `PLANNED` | Non-RC4; odd clan XML response |
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
| `POST ProfileStats.asmx/ReadStatsByGamer2` | stats S3 + lookup | `PLANNED` | Packed binary stats |
| `POST ProfileStats.asmx/ReadStatsByGroups` | stats S3 + lookup | `PLANNED` | PS5 uses `Gen9StatLookup` |
| `POST ProfileStats.asmx/WriteStats` | stats S3 | `PLANNED` | Merge custom binary format |
| `POST ProfileStats.asmx/ResetStats` | static | Tranche 1 | Status-only response |
| `POST SaveMigration.asmx/GetSourceAccountsMP` | static | `PLANNED` | Empty accounts |
| `POST SaveMigrationSingleplayer.asmx/GetUploadedSingleplayerSaveMetadata` | session key | `PLANNED` | Never return raw keys |
| `POST telemetry.asmx/GetTelemetryClientConfig` | static | Tranche 1 | Config attributes on root |
| `POST telemetry.asmx/SubmitCompressed` | compressed log | Tranche 1 | Validate/decompress bounded payload, no log body |
| `POST ugc.asmx/CheckText` | static | Tranche 1 | Shared with Social Club |
| `POST ugc.asmx/CreateContent\|UpdateContent` | UGC DB + S3 | `PLANNED` | Custom multipart binary |
| `POST ugc.asmx/Publish\|SetDeleted` | UGC DB | `PLANNED` | Ownership checks |
| `POST ugc.asmx/QueryContent` | UGC DB + static heists | `PLANNED` | JSON5 query params |

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

## Schema v8 tables

`TESTED`: schema v8 is implemented in `database.py` and covers:

- `news`, `social_messages` (presence-delivery style), `ugc_contents`, `ugc_mission_bookmarks`,
  `ugc_mission_playlists`, `ugc_mission_playlist_items`;
- `tunables`, `stat_lookups`, `gen9_stat_lookups`;
- `clans`, `clan_ranks`, `clan_memberships`, `clan_invites`, `clan_join_requests`,
  `clan_wall_messages`;
- `active_players`, `user_ip_addresses`, `launch_verifications`;
- per-account ordered `presence_attributes` (`value_type`, `value_text`,
  `sort_order`) and `users.primary_clan_id` for primary-clan state.

`TESTED`: the local/S3-compatible adapter in `object_storage.py` validates every
key component, scopes saves and shares to the authenticated internal account id,
and keeps separate bucket profiles for UGC and news. S3 credentials and the
photo-upload HMAC secret are environment-only. No production bucket names,
credentials, or hardcoded encryption keys were copied into the repository.

`UNVERIFIED`: the Android client has not yet been observed calling these cloud
routes. Upstream RC4 response framing is deliberately not ported; native
authentication uses the existing ticket/session-ticket plumbing.
