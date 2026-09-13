# Deploying the backend

The server pulls an image built by CI; nothing is built on the server.

## Layout

    /opt/alltech/backend/
      compose.prod.yml     from this repo
      .env                 not in git -- see .env.example
      scheduler.env        for the scheduled jobs

## First time

```bash
sudo mkdir -p /opt/alltech/backend && cd /opt/alltech/backend
# copy compose.prod.yml and create .env from .env.example
docker compose -f compose.prod.yml pull
docker compose -f compose.prod.yml up -d
curl -fsS http://localhost:8000/api/health/
```

`docker compose` reads `compose.yml` by default, so either pass
`-f compose.prod.yml` every time or name the file `compose.yml` on the server.
The deploy workflow assumes the latter.

## What has to be true before it works

- **`SECURE_SSL_REDIRECT=False` until TLS is in front of it.** With it on and
  no proxy setting `X-Forwarded-Proto: https`, every request redirects to https,
  comes back as http, and loops until the browser gives up. Turn it back on the
  moment certificates are installed -- HSTS is meaningless without it.
- **`SERVER_URL` and `SERVER_IP` must be set**, or `ALLOWED_HOSTS` rejects the
  request with a bare `400` and an HTML body that explains nothing.
- **`DB_PORT` should be Supabase's session-mode port (5432)**, not the
  transaction pooler (6543), whenever `DB_SCHEMA` is set.
- **The origin must not be reachable except through the proxy.** The app trusts
  `X-Forwarded-Proto` to decide whether a request arrived over TLS; a directly
  reachable origin lets any client forge it. `compose.prod.yml` binds port 8000
  to loopback for this reason -- keep it that way and firewall 8000.

## Scheduled jobs

Run by the server's cron, one container per job, exiting when done. The
scheduler belongs on **this** server, not the frontend one: it calls this API
over the Docker network rather than out through Cloudflare and back.

See `deploy/crontab.example` in the scheduler repository.

## Log rotation

`compose.prod.yml` caps container logs at 3 x 10MB each. Without that the
default json-file driver grows until the disk is full, which on a small VPS
takes weeks, not years. nginx's own logs are the server's to rotate; a
logrotate entry for `/var/log/nginx/*.log` and
`/var/log/alltech-scheduler.log` covers it.

## Rolling back

Images are tagged with the commit sha as well as the branch:

```bash
docker compose -f compose.prod.yml pull
# or pin an exact build
docker run ... gachar4/alltech-backend:<sha>
```
