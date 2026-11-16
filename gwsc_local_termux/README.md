# GWSC Local Game Server (Termux Deployment Package)

Complete, fully-functional local backend replacement for the Android GTA V / GWSC multiplayer port.

## Features

- **Full GWSC API Implementation**:
  - `GET /gwsc/api/info` - Server diagnostic, online players count, registration status.
  - `POST /gwsc/api/register` - User registration with password hashing & auto Rockstar ID generation.
  - `POST /gwsc/api/login` - Authentication, token generation, and profile initialization.
  - `POST /gwsc/api/logout`, `POST /gwsc/api/logout-all` - Token invalidation and session management.
  - `GET /gwsc/api/me`, `PATCH /gwsc/api/me` - Profile retrieval and nickname updates.
  - `POST /gwsc/api/me/password` - Password changing.
  - `GET /gwsc/api/avatars/presets`, `POST /gwsc/api/me/avatar/preset` - Avatar preset colors.
  - `GET /gwsc/api/users/{id}/profile` - Full public profiles, GTA Online character stats (rank, wallet, bank), achievements.
  - `POST /gwsc/api/users/{id}/block`, `DELETE /gwsc/api/users/{id}/block` - User blocking/unblocking.
  - `GET /gwsc/api/friends`, `POST /gwsc/api/friends` - Social friends system with request management.
  - `POST /gwsc/api/friends/{id}/accept`, `DELETE /gwsc/api/friends/{id}` - Friend request acceptance and removal.
  - `GET /gwsc/api/conversations`, `POST /gwsc/api/conversations` - Direct and group conversations.
  - `GET /gwsc/api/conversations/{id}/messages`, `POST /gwsc/api/conversations/{id}/messages` - Message history and sending.
  - `POST /gwsc/api/conversations/{id}/read` - Read status tracking.
  - `DELETE /gwsc/api/conversations/{id}/members/{rockstarId}` - Group member removal.
  - `GET /gwsc/api/sharkcards`, `POST /gwsc/api/sharkcards/{id}` - In-game bank funds crediting.
  - `POST /gwsc/api/starterpack` - Criminal Enterprise Starter Pack ($1,000,000 bonus).
  - `GET /gwsc/api/events` - Server-Sent Events (SSE) real-time push streaming.

- **Native ROS / RAGE Services**:
  - Native `CreateTicket` XML handler returning valid session ticket and account metadata.
  - `RosGamersSettings.json` config service.
  - Presence commitment and matchmaking lobby session advertisement / discovery.

- **Multiplayer Networking**:
  - Asynchronous UDP Multiplayer Relay on port 6672 forwarding peer packets between clients.

- **Storage & Security**:
  - Persistent SQLite database at `data/gwsc.db`.
  - Self-signed SSL certificate generator with Subject Alternative Names (SAN) for `127.0.0.1`, `localhost`, and LAN IPs.
  - Automatic database backups at `backups/`.

---

## Installation & Running on Termux

### 1. Run Setup
```sh
cd gwsc_local_termux
./install.sh
```

### 2. Start the Server
```sh
./run.sh
```

### 3. Check Server Status
```sh
curl -k https://127.0.0.1:8443/
curl -k https://127.0.0.1:8443/gwsc/api/info
```

### 4. Stop or Restart
```sh
./stop.sh
./restart.sh
```

### 5. Database Backup & Restore
```sh
# Create a backup
python -m gwsc_local_termux.backup

# Restore from a backup
python -m gwsc_local_termux.backup restore backups/gwsc_backup_YYYYMMDD_HHMMSS.db
```
