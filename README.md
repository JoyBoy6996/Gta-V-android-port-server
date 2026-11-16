# GTA V Android Port - GWSC Local & Cloud Server

Complete, functional backend replacement and private server for the Grand Theft Auto V Android port with Gooneral Wheelchair Social Club (GWSC) integration.

Supports running locally in **Android Termux** and cloud deployment on **[Render](https://render.com)**.

---

## Architecture Overview

1. **GWSC REST & SSE API (`/gwsc/api/*`)**:
   - Authentication (`/register`, `/login`, `/logout`, `/logout-all`)
   - Profile management (`/me`, `/me/password`, `/me/avatar/preset`, `/avatars/presets`)
   - User public profiles (`/users/{id}/profile`, `/users/{id}/block`)
   - Social system (`/friends`, `/friends/{id}/accept`, `/friends/{id}`)
   - Real-time messaging & conversations (`/conversations`, `/messages`, `/read`)
   - In-game economy (`/sharkcards`, `/starterpack`)
   - Server-Sent Events (SSE) live push notifications (`/events`)

2. **Native RAGE / ROS Services**:
   - `CreateTicket` session authentication & Rockstar ID token assignment (XML).
   - `RosGamersSettings.json` config service.
   - Matchmaking lobby advertisement (`MatchmakingAdvertiseTask`) and discovery (`MatchmakingFindTask`).
   - Player presence synchronization (`CommitPresence`).

3. **Multiplayer Networking**:
   - Asynchronous UDP Relay on port `6672` for relaying gameplay synchronization packets between connected game clients.

---

## Deployment on Render

This repository is ready for one-click or repository deployment on [Render](https://render.com):

### Option A: Using Render Blueprint (`render.yaml`)
1. Create a new account on [Render](https://render.com).
2. Go to **Blueprints** → **New Blueprint Instance**.
3. Connect your GitHub repository `Gta-V-android-port-server`.
4. Click **Apply**. Render will automatically detect `render.yaml` and deploy the service.

### Option B: Manual Web Service Setup
1. In Render Dashboard, click **New +** → **Web Service**.
2. Connect your GitHub repository.
3. Set the following settings:
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn gwsc_local_termux.server:app --host 0.0.0.0 --port $PORT`
4. Click **Create Web Service**.

Your server will be accessible at:
`https://<your-service-name>.onrender.com/`

---

## Running Locally in Termux

### 1. Initialize
```bash
cd gwsc_local_termux
./install.sh
```

### 2. Start / Stop / Restart
```bash
./run.sh       # Start server in background
./stop.sh      # Stop server
./restart.sh   # Restart server
```

### 3. Check Status
```bash
curl -k https://127.0.0.1:8443/gwsc/api/info
```

---

## Connecting the Android Game

To point your Android game client to your server:

1. In `assets/gwsc.properties`, set:
   - For Render cloud deployment:
     ```properties
     SERVER_NAME=GWSC Cloud Server
     SERVER_HOST=<your-render-app>.onrender.com
     WEB_URL=https://<your-render-app>.onrender.com/
     SELF_SIGNED_CERT=0
     ```
   - For local Termux deployment:
     ```properties
     SERVER_NAME=GWSC Local Server
     SERVER_HOST=127.0.0.1:8443
     WEB_URL=https://127.0.0.1:8443/
     SELF_SIGNED_CERT=1
     ```
2. Rebuild and sign the APK with `python build_modified_apk.py`.
3. Install and launch the game on your Android device.
