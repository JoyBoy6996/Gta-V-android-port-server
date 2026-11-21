# Setup: Termux (Android)

This is the primary development workflow. It runs the server directly on the
Android device with a self-signed HTTPS certificate.

## Prerequisites

Install from F-Droid or the Termux GitHub releases (the Play Store build is
outdated):

```bash
pkg update
pkg install python openssl git
```

`python3` and `openssl` are required by `install.sh`.

## Install

```bash
cd /data/user/0/com.termux/files/home/gtaserver/gwsc_local_termux
./install.sh
```

`install.sh` will:

1. Check `python3` and `openssl`.
2. Remove a broken `.venv` symlink from older packages.
3. Create `.venv` and install `requirements.txt`.
4. Generate `cert.pem` / `key.pem` with SANs for `127.0.0.1`, `localhost` and
   local LAN addresses.
5. Initialise `data/gwsc.db` (running forward migrations on legacy databases).
6. Tighten permissions on `data/`, `logs/`, `backups/`, `cert.pem`, `key.pem`.

For tests, install dev dependencies too:

```bash
./install.sh --dev
```

## Run

```bash
./run.sh        # start in the background, writes server.pid
./stop.sh       # stop
./restart.sh    # restart
```

The server listens on `https://0.0.0.0:8443` and the UDP relay on
`0.0.0.0:6672` by default.

## Verify

```bash
curl -k https://127.0.0.1:8443/
curl -k https://127.0.0.1:8443/health
curl -k https://127.0.0.1:8443/gwsc/api/info
```

`/gwsc/api/info` reports `online: 0` when nobody is connected. That is a real
count, not a placeholder.

## Register and log in

```bash
curl -k -X POST https://127.0.0.1:8443/gwsc/api/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"player@example.invalid","nickname":"player","password":"a long test password"}'
```

Use a throwaway password. Do not reuse a real credential. The response shape
(`{token, profile}`) is `UNVERIFIED` against the game client; it is a private
contract until a capture proves otherwise.

## Configuration

Copy `.env.example` to `.env` in the repository root, or export variables
before `./run.sh`. The server reads environment variables at process start.
See [`environment-variables.md`](environment-variables.md).

Common local overrides:

```bash
export GWSC_PORT=8443
export GWSC_RELAY_PORT=6672
export GWSC_REGISTRATION=true
export GWSC_CAPTURE_ENABLED=true   # development capture, never in production
```

## Connecting the game

Point the modified client at `https://<device-lan-ip>:8443/`. A self-signed
certificate usually requires `SELF_SIGNED_CERT=1` in the client config and a
rebuild of the APK. Building APKs is outside this repository's scope.

**Do not assume compatibility.** The client has only been observed sending a
login request. Successful login, profile rendering, native tasks and
gameplay are not yet verified.

## Logs and data

| Path | Contents |
| --- | --- |
| `gwsc_local_termux/logs/gwsc.log` | Application log (redacted). |
| `gwsc_local_termux/data/gwsc.db` | SQLite database (chmod 0600). |
| `gwsc_local_termux/backups/` | Database backups. |
| `gwsc_local_termux/captures/` | Development captures (only when enabled). |

## Troubleshooting

See [`operations/troubleshooting.md`](operations/troubleshooting.md). Common
issues:

- **`python3`/`openssl` missing** — `pkg install python openssl`.
- **Port already in use** — `./stop.sh`, or set `GWSC_PORT`.
- **Certificate warnings** — expected with a self-signed cert; verify SANs.
- **Legacy tokens rejected** — intentional; the migration invalidates
  plaintext tokens. Log in again.
