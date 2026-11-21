# Setup: VPS / Docker

Two supported production-ish paths: Docker Compose and a systemd service
behind nginx. Both terminate TLS at a reverse proxy, so the app itself runs
plain HTTP on a private interface.

> **Scope warning:** this backend has not been validated against the real
> Android client. Treat any internet-facing deployment as an experiment, not
> a production game service.

## Option A: Docker Compose

```bash
cp .env.example .env      # optional; compose sets the important values
docker compose up -d --build
docker compose logs -f gwsc
```

`compose.yaml`:

- publishes `8443/tcp` and `6672/udp`;
- runs with `GWSC_ENV=production`, `GWSC_SSL_ENABLED=false`;
- persists `/data`, `/logs`, `/backups` as named volumes;
- has a `/health` healthcheck.

Put a TLS-terminating proxy (nginx, Caddy, Traefik) in front of `8443`. Set
`GWSC_TRUST_PROXY=true` only when the proxy is trusted.

```bash
curl http://127.0.0.1:8443/health
```

## Option B: systemd + nginx

Assumptions: Debian/Ubuntu, app at `/opt/gwsc`, service user `gwsc`.

```bash
sudo useradd --system --home /opt/gwsc --shell /usr/sbin/nologin gwsc
sudo mkdir -p /opt/gwsc /etc/gwsc /var/lib/gwsc /var/log/gwsc /var/backups/gwsc
sudo chown -R gwsc:gwsc /opt/gwsc /var/lib/gwsc /var/log/gwsc /var/backups/gwsc
```

Copy the repository to `/opt/gwsc`, then:

```bash
cd /opt/gwsc
sudo -u gwsc python3 -m venv .venv
sudo -u gwsc .venv/bin/pip install -r requirements.txt
sudo install -m 0640 -o root -g gwsc deploy/gwsc.env.example /etc/gwsc/gwsc.env
sudo install -m 0644 deploy/gwsc.service /etc/systemd/system/gwsc.service
sudo systemctl daemon-reload
sudo systemctl enable --now gwsc
```

Edit `/etc/gwsc/gwsc.env` first. Set `GWSC_PUBLIC_HOST` to the public
hostname so matchmaking advertises a reachable address.

`deploy/gwsc.service` runs with `ProtectSystem=strict`, `ProtectHome=true`,
`PrivateTmp=true` and a limited capability set. The writable paths are
`/var/lib/gwsc`, `/var/log/gwsc` and `/var/backups/gwsc`.

### nginx

```bash
sudo install -m 0644 deploy/nginx.conf.example /etc/nginx/sites-available/gwsc
sudo ln -s /etc/nginx/sites-available/gwsc /etc/nginx/sites-enabled/gwsc
sudo nginx -t && sudo systemctl reload nginx
```

Replace `gwsc.example.com` and the certificate paths. The example:

- redirects HTTP to HTTPS;
- proxies `/` to `127.0.0.1:8443`;
- disables buffering for `/gwsc/api/events` (SSE);
- allows a 2 MB request body.

The UDP relay is not proxied. Open it directly:

```bash
sudo ufw allow 6672/udp
```

TLS certificates can come from Let's Encrypt (`certbot --nginx`).

## Firewall

Only expose what clients need:

| Port | Protocol | Purpose |
| --- | --- | --- |
| 443 | TCP | HTTPS API + SSE (via proxy) |
| 6672 | UDP | Relay |

Keep `8443` bound to `127.0.0.1` when a proxy is in front.

## Backups and monitoring

- Backups: [`operations/backups.md`](operations/backups.md).
- Monitoring: [`operations/monitoring.md`](operations/monitoring.md).

## Scaling notes

The current design is single-process SQLite:

- Rate limits and the SSE bus are in-memory.
- The UDP relay registry is in-memory.
- WAL helps concurrent readers but there is one writer.

For larger deployments you would need PostgreSQL, a shared rate limiter, a
shared event bus and a relay design that can scale horizontally. That work is
not implemented. Do not run multiple Uvicorn workers against the current
relay/SSE/rate-limit design expecting correct behaviour.

## Production checklist

- [ ] `GWSC_ENV=production` (disables capture and docs by default).
- [ ] `GWSC_CAPTURE_ENABLED=false`.
- [ ] `GWSC_DOCS_ENABLED=false`.
- [ ] Strong, stable `GWSC_TOKEN_PEPPER` (changing it invalidates all tokens).
- [ ] TLS terminated by a trusted proxy.
- [ ] `GWSC_PUBLIC_HOST` set for matchmaking.
- [ ] Firewall allows only 443/tcp and the relay UDP port.
- [ ] Backups scheduled and tested.
- [ ] Logs monitored and redaction verified.
- [ ] Acknowledged that client compatibility is unverified.
