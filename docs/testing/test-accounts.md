# Test Accounts

**Never commit real credentials, tokens, cookies or production captures.**
This document describes how to create throwaway accounts safely.

## Automated tests

Tests create accounts per test via the `make_account` fixture in
`tests/conftest.py`:

| Field | Value |
| --- | --- |
| Email | `player@example.invalid` |
| Nickname | `player` |
| Password | `correct horse battery` |

These values exist only in the test process, use the reserved
`.invalid` domain, and are written to a temporary database created with
`tempfile.mkdtemp()`. They are not secrets and are not usable against a real
deployment.

Run the suite:

```bash
GWSC_SSL_ENABLED=false GWSC_RELAY_PORT=0 \
  gwsc_local_termux/.venv/bin/python -m pytest -q
```

`tests/conftest.py` sets `DATA_DIR` to a temporary directory, disables TLS,
disables the UDP relay listener and disables capture before importing the
app (configuration is read at import time).

## Manual development account

Create a disposable account through the API:

```bash
curl -k -X POST https://127.0.0.1:8443/gwsc/api/register \
  -H 'Content-Type: application/json' \
  -d '{
    "email": "dev-player@example.invalid",
    "nickname": "devplayer",
    "password": "replace with a long unique password",
    "client": "launcher"
  }'
```

Guidelines:

- Use the reserved `example.invalid` / `example.com` domains.
- Use a unique password that is not reused anywhere.
- Keep the returned token out of shell history where possible
  (`read -s`, a password manager, or an environment file with mode 0600).
- Do not paste tokens into issues, docs or chat.

## Resetting local accounts

Because the local database is disposable in development, the simplest reset
is a fresh database. **Stop the server and back up first.**

```bash
./gwsc_local_termux/stop.sh
python -m gwsc_local_termux.backup          # safety copy
mv gwsc_local_termux/data/gwsc.db \
   gwsc_local_termux/data/gwsc.db.bak
./run.sh                                    # recreates an empty schema
```

Do not delete a database you have not backed up. Do not run this against a
deployment with real users.

## Disabling registration

On a private server you can close registration after creating accounts:

```bash
export GWSC_REGISTRATION=false
./restart.sh
```

Registration then returns HTTP 403 `registration_disabled`.

## Notes on credentials

- Passwords are stored as PBKDF2-HMAC-SHA256 hashes, never plaintext.
- Bearer tokens are stored as SHA-256 hashes, never plaintext.
- Native tickets are stored as SHA-256 hashes.
- Legacy plaintext tokens from older builds are invalidated by migration;
  log in again to get a new token.
- Changing `GWSC_TOKEN_PEPPER` invalidates every existing token.
