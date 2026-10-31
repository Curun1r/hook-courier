# Deployment preparation

This configuration has been rehearsed locally. No public service, paid hosting,
domain, or TLS certificate has been provisioned. Hosting selection is deferred.

## Runtime

- One image contains Gunicorn, the Django API, the worker code, and collected static assets.
- The image runs as user `hookcourier` (UID 10001), not root.
- Gunicorn starts two synchronous workers by default. `WEB_CONCURRENCY` controls the count.
- WhiteNoise serves compressed, content-hashed dashboard and Admin files with DEBUG disabled.
- A separate `migrate` service must exit successfully before web and worker start.
- PostgreSQL and Redis have persistent, project-scoped volumes and no published ports.
- The HTTP port is bound to `127.0.0.1:18001` by default.
- The demo receiver starts only with `--profile demo` and has no published port.
- `/healthz/` checks the database and returns only an availability status. It deliberately
  does not depend on Redis: the API can still save pending events during a broker outage.

Static files are collected when building the image with build-only settings; no runtime
secret is needed or embedded at build time. Gunicorn access logs exclude query strings,
headers, and bodies. Outgoing HTTP client logs remain suppressed.

## Prepare the environment

```bash
cp .env.production.example .env.production
```

Generate separate secrets for Django, the API key, and PostgreSQL, for example:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Replace all `change-this-...` values. The production settings reject short keys,
wildcard hosts, and SQLite. Keep this file private; Git and the image context exclude it.
Do not overwrite an existing configured environment file or rotate a database password
without planning the corresponding PostgreSQL account change.

**Every production Compose command must include `--env-file .env.production`.** This
keeps Compose's interpolated database values aligned with the application's environment.
For a differently named file, also set `HOOKCOURIER_ENV_FILE` to that path.

## Rehearse locally without TLS

For this loopback-only rehearsal, set these values in `.env.production`:

```dotenv
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DJANGO_HTTPS=0
DJANGO_TRUST_PROXY=0
HOOKCOURIER_HTTP_PORT=18001
```

Use a different Compose project from development so its data remains separate:

```bash
docker compose --env-file .env.production -p hookcourier-prod -f compose.production.yaml --profile demo up --build -d --wait
docker compose --env-file .env.production -p hookcourier-prod -f compose.production.yaml ps -a
docker compose --env-file .env.production -p hookcourier-prod -f compose.production.yaml exec web python src/manage.py createsuperuser
```

`migrate` should be exited with code 0; web, worker, PostgreSQL, Redis, and the optional
receiver should be running. Open <http://localhost:18001/>. Create an active destination
with URL `http://receiver.local:9000/webhook/` and repeat the README demo, using port
18001 and the API key from `.env.production`. Source that file for curl examples instead
of the development `.env`. Refresh the event page to see 503, 503, 200 and delivered.

Management commands use the same Compose prefix, for example:

```bash
docker compose --env-file .env.production -p hookcourier-prod -f compose.production.yaml exec web python src/manage.py requeue_pending --dry-run
docker compose --env-file .env.production -p hookcourier-prod -f compose.production.yaml exec web python src/manage.py delivery_report --from-date 2026-10-24 --to-date 2026-10-24
```

Stop the rehearsal while retaining its database:

```bash
docker compose --env-file .env.production -p hookcourier-prod -f compose.production.yaml down
```

Only disposable test databases should be removed with `down --volumes`.

## Before a public launch

Use a dedicated hostname, a TLS-terminating reverse proxy, and fresh deployment secrets.
The proxy must forward only allowed hosts and replace any client-supplied forwarding
headers. Keep Gunicorn reachable only through that trusted proxy.

Set `DJANGO_ALLOWED_HOSTS` to the real hostname, `DJANGO_CSRF_TRUSTED_ORIGINS` to its HTTPS
origin, and `DJANGO_HTTPS=1`. Set `DJANGO_TRUST_PROXY=1` only once that proxy boundary is
configured. The localhost health probe sets an allowed Host header and is exempt from
HTTPS redirection. All application paths still require HTTPS when enabled.

Run the production checks with HTTPS enabled:

```bash
docker compose --env-file .env.production -p hookcourier-prod -f compose.production.yaml run --rm migrate python src/manage.py check --deploy
```

The initial HSTS policy is one hour, without subdomain coverage or preloading. Django
therefore reports advisory warnings `security.W005` and `security.W021`. Decide on those
policies only after the actual hostname and HTTPS deployment have been verified.
The HTTP-only local rehearsal additionally reports disabled HTTPS/cookie protections;
those settings must not be used for a public launch.

Destination configuration remains restricted to the trusted owner. Keep API keys private;
the project does not provide anonymous event submission or public registration.

## Updates, backups, and rollback

Back up PostgreSQL before an update that changes the schema:

```bash
mkdir -p "$HOME/hookcourier-backups"
chmod 700 "$HOME/hookcourier-backups"
HOOKCOURIER_BACKUP_PATH="$HOME/hookcourier-backups/hookcourier-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose --env-file .env.production -p hookcourier-prod -f compose.production.yaml exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$HOOKCOURIER_BACKUP_PATH"
chmod 600 "$HOOKCOURIER_BACKUP_PATH"
```

Keep backups outside the repository and validate restoration in a separate database.
Restoration procedure for that separate destination database:

```bash
docker compose --env-file .env.production -p hookcourier-prod -f compose.production.yaml exec -T db sh -c 'pg_restore --no-owner -U "$POSTGRES_USER" -d restore_database' < "$HOOKCOURIER_BACKUP_PATH"
```

Create `restore_database` before that restore; do not point the command at the application
database during an experiment. Stop web and worker before applying an incompatible
migration. Rebuild the selected release, run its migrations once, and start the services.
Reverting Git commits does not restore the database or persistent volumes. Migration
0003 backfills completion times and adds a report index; reversing it removes those data.

Worker shutdown has a 45-second grace period. A 15-second HTTP deadline is backed by
Celery soft/hard limits at 20/25 seconds by default (the hard limit requires a pool
with time-limit support, such as production's prefork pool). Delivery claims expire
after 75 seconds; each claim has its own token and durable attempt record.

Before applying migration 0004, drain/stop old workers, run migrations once, then
restart web and workers together. Historical completed attempts receive a completion
time, and legacy delivering events receive a 120-second recovery grace period.
Do not roll back the lease schema while these workers are running.

After a forced interruption, inspect the receiver and preview recovery with
`requeue_pending --recover-expired --dry-run`. Use `--recover-expired --limit 100`
to retire expired claims, retain their unknown-outcome history and queue another
attempt within the existing budget. Late worker results are ignored after recovery.
Receivers still need event-ID deduplication: a timeout or missing result does not prove
that no side effect occurred. Schedule this command externally if automatic periodic
recovery is required; the application does not start a scheduler.

## References

- [Django deployment checklist](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/)
- [WhiteNoise with Django](https://whitenoise.readthedocs.io/en/stable/django.html)
- [Gunicorn settings](https://gunicorn.org/reference/settings/)
