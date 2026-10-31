# HookCourier

HookCourier accepts JSON events and delivers them to an owner-configured HTTP endpoint in a background worker. It records each delivery attempt, retries temporary failures, and lets the owner inspect the result.

The main demo is deliberately simple: the receiver returns **503, 503, then 200**. The event reaches `delivered` with three recorded attempts.

```text
Sender → Django REST API → PostgreSQL → Redis → Celery worker → HTTP receiver
                               ↑                    │
                               └── attempt history ─┘
```

## What is included

- API-key protected event acceptance and status endpoints.
- PostgreSQL storage for destinations, events, and individual attempts.
- Idempotency keys: equivalent replays return the same event; conflicting data returns 409.
- HTTP timeouts, bounded retries, safe logs, and a pending-event recovery command.
- An owner dashboard with status filtering, pagination, payloads, and attempt history.
- A PostgreSQL report by destination and UTC completion day, with measured query plans.
- GitHub Actions for lint, formatting, configuration, migrations, and PostgreSQL tests.
- Development Compose and a production rehearsal using Gunicorn, WhiteNoise, and a non-root image.

Scope: one trusted owner and HTTP delivery. Public registration, multiple organizations, file uploads, extra delivery channels, and a complex frontend are excluded. No public hosting is provisioned yet.

## Quick start

Requires Git and Docker Compose v2 with Docker running. Python 3.13+ is needed only for host-side tests or generating secrets.

1. Copy `.env.example` to `.env`. If you already configured `.env`, retain it.
2. Replace **every** `change-this-...` value with a separate random value. Generate values with `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`. Keep secrets out of Git.
3. Start the development services:

   ```bash
   docker compose up --build -d --wait
   docker compose ps
   docker compose exec web python src/manage.py createsuperuser
   ```

4. Open the [owner dashboard](http://localhost:8000/). Use the active superuser account; login reuses Django Admin.
5. In [Admin](http://localhost:8000/admin/), create an active destination named `Retry demo receiver` with URL `http://receiver.local:9000/webhook/`. Note its numeric ID.

The development stack uses Django's development server. PostgreSQL, Redis, and the receiver have no published host ports. Existing accounts and events persist in Docker volumes.

## Repeat the demo

From the repository directory, load the key and send an event. Replace `destination_id: 1` with the numeric ID you just created. Use a fresh idempotency key for each new demo run.

```bash
source .env
curl -i http://localhost:8000/api/v1/events/ \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $HOOKCOURIER_API_KEY" \
  -H 'Idempotency-Key: demo-retries-001' \
  -d '{"destination_id":1,"event_type":"appointment.created","payload":{"appointment_id":123}}'
```

Expect `202 Accepted`, a UUID in `id`, and a `Location` header. Replace `EVENT_UUID` with that ID:

```bash
curl -s http://localhost:8000/api/v1/events/EVENT_UUID/ \
  -H "X-API-Key: $HOOKCOURIER_API_KEY" | python3 -m json.tool
```

After about six seconds plus request time, expect `status: delivered` and attempt codes `[503, 503, 200]`. Open the event in the dashboard and use **Refresh** to see updates.

Send the identical POST again: it returns the same UUID with `Idempotency-Replayed: true` and creates no new delivery. Change `appointment_id` while retaining the key: expect `409 Conflict`. Omitting the key creates a new event each time. Receiver counters reset when its container restarts.

## API and data

| Endpoint | Result | Authentication |
|---|---|---|
| `POST /api/v1/events/` | Validate and accept an event; return 202 | `X-API-Key` |
| `GET /api/v1/events/{uuid}/` | Event state and all attempts | `X-API-Key` |
| `/` and `/events/{uuid}/` | Owner dashboard | Active superuser session |
| `GET /healthz/` | Database availability; 200 or 503 | None; no private details |

`Destination` stores the endpoint name, URL, and active flag. `Event` stores the destination, type, JSON payload, idempotency fingerprint, state, next retry, and completion time. `DeliveryAttempt` stores its number, start time, HTTP code, duration, and safe error.

A destination ID is an integer. An event ID is a UUID and stays unchanged across retries. The receiver gets `id`, `event_type`, and `payload`, plus an `X-HookCourier-Event-ID` header. The sender's API key is never forwarded. Attempts include `finished_at`; it is null while a request is in progress. Invalid UTF-8 strings and null characters in payload values or object keys return HTTP 400.

## Delivery rules

| State | Meaning |
|---|---|
| `pending` | Waiting for a first attempt, retry, or queue recovery |
| `delivering` | Claimed by a worker for an HTTP request |
| `delivered` | Receiver returned HTTP 2xx |
| `failed` | Permanent error or exhausted attempt limit |

- Retry network errors, timeouts, HTTP 429, and HTTP 5xx.
- Other HTTP 4xx and redirects fail immediately; redirects are not followed.
- Five attempts maximum by default, including the first. Retry delays: 2, 4, 8, 16 seconds.
- Limit the outgoing JSON envelope to 65,536 bytes. API and worker both check it.
- Use a five-second timeout for each HTTP I/O phase and a 15-second total HTTP deadline; response bodies are not stored.
- Reserve an attempt before HTTP and retain interrupted attempts in history. Expired delivery leases can be recovered explicitly; late worker results cannot overwrite a newer attempt.
- A disabled destination produces a failed attempt without an HTTP request.
- Publish the task with `transaction.on_commit()` after PostgreSQL saves the event.
- Database uniqueness and row locks protect idempotency and concurrent worker claims.

A receiver can process the same event more than once if its response is lost. HookCourier **does not promise exactly one delivery**. Receivers should deduplicate using the immutable event ID.

## Recover after a queue outage

If Redis is unavailable, the API still saves the event and returns 202. Preview due pending events and queue them after restoring Redis:

```bash
docker compose exec web python src/manage.py requeue_pending --dry-run
docker compose exec web python src/manage.py requeue_pending --limit 100
```

The default batch is 100, oldest first. Future retries and delivering/terminal states are skipped by default. Dry-run publishes nothing. A publish failure stops with a nonzero exit status and retains saved data. Repeated recovery can enqueue duplicate jobs; the worker checks the saved state before delivery.

After a worker crash, inspect the receiver and preview expired delivery leases:

```bash
docker compose exec web python src/manage.py requeue_pending --recover-expired --dry-run
docker compose exec web python src/manage.py requeue_pending --recover-expired --limit 100
```

This is an explicit opt-in to retry deliveries whose receiver outcome may be unknown.
Receivers must deduplicate by the immutable event ID. Fresh leases and terminal events
are left alone. Interrupted attempts consume the same five-attempt budget as HTTP
failures. At the limit the event becomes failed rather than starting another request.
A token check prevents an older worker's late response from replacing a newer result.
This command should be scheduled by the deployment operator; no scheduler is started
automatically.

## Daily report

```bash
docker compose exec web python src/manage.py delivery_report \
  --from-date 2026-10-01 --to-date 2026-10-24
```

CSV counts **terminal events**, grouped by destination ID and UTC completion day; both dates are inclusive. Add `--destination-id 2` to filter or `--explain` to execute `EXPLAIN (ANALYZE, BUFFERS)`.

The dates above are examples. Select the UTC day on which your event completed to include it in the result.

The report has a partial covering index for completion-time ranges. A documented 100,000-event local benchmark changed from a sequential scan to an index-only scan: median 20.246 ms to 0.385 ms for the selected one-day range. These measurements describe that dataset, not a production latency guarantee. See [SQL, migration semantics, measured plans, and reproduction](docs/reporting.md).

## Tests and CI

Install host-side development tools:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

Fast SQLite run; PostgreSQL-only SQL and locking cases are explicitly skipped:

```bash
DB_ENGINE=sqlite DJANGO_DEBUG=1 .venv/bin/python -m pytest -q -ra --strict-markers
```

Full local PostgreSQL run, using an isolated disposable database on port 25432:

```bash
docker compose -p hookcourier-tests -f compose.test.yaml up -d --wait
DB_ENGINE=postgresql DJANGO_DEBUG=1 DB_HOST=127.0.0.1 DB_PORT=25432 \
  POSTGRES_DB=hookcourier_test POSTGRES_USER=hookcourier_test \
  POSTGRES_PASSWORD=disposable-test-password \
  .venv/bin/python -m pytest -q -ra --strict-markers
docker compose -p hookcourier-tests -f compose.test.yaml down --volumes
```

The suite covers acceptance, replay/conflict, simultaneous idempotency requests, real PostgreSQL worker locking, retries, final errors, queue outages, owner access, escaped payloads, reporting boundaries/backfill, health, and production settings. Unit tests mock the broker and HTTP receiver. The separate Compose demo verifies real Redis and Celery.

[GitHub Actions](.github/workflows/ci.yml) runs on pushes and pull requests with PostgreSQL 17 and Python 3.13. Actions are pinned to release commit SHAs and receive read-only repository permissions. A workflow run becomes available after the branch is pushed; local tests alone do not establish GitHub CI status.

## Deployment preparation

See [production rehearsal, HTTPS, backups, and updates](docs/deployment.md). Production uses Gunicorn, WhiteNoise, an unprivileged user, a separate migration job, and an isolated database/Redis network. The default web binding is loopback. Public hosting, a domain, and TLS setup remain to be selected; this repository does not claim a live public demo.

## Configuration and operational limits

`.env.example` lists development settings; `.env.production.example` lists production settings. Delivery defaults are `WEBHOOK_MAX_ATTEMPTS=5`, `WEBHOOK_TIMEOUT_SECONDS=5`, `WEBHOOK_MAX_BYTES=65536`, `WEBHOOK_RETRY_BASE_SECONDS=2`, and `WEBHOOK_RETRY_MAX_SECONDS=60`. `WEBHOOK_DEADLINE_SECONDS=15` bounds the HTTP operation. Celery soft/hard limits are the deadline plus 5/10 seconds; delivery leases last the deadline plus 60 seconds. Recreate web and worker containers after changing environment values.

Destination configuration is restricted to the trusted owner. Accepting URLs from untrusted users requires separate SSRF protection. Dashboard pages require an active superuser, escape untrusted text, and are private/non-cacheable. API keys, payloads, response bodies, and receiver credentials are excluded from delivery logs.

A forced worker interruption can leave an event `delivering` until its lease expires. Inspect receiver behavior and use `requeue_pending --recover-expired` for controlled recovery. Lost broker messages also require the recovery command; no outbox dispatcher or recovery scheduler is included. See the deployment guide for the operational procedure.

## Repository structure

```text
src/config/                 Django, Celery, health, production/static settings
src/deliveries/             API, models, delivery rules, reporting, owner dashboard
src/deliveries/management/  Recovery and report commands
src/deliveries/migrations/  Schema, historical backfill, indexes
demo/receiver.py            Receiver that fails twice, then succeeds
tests/                      API, delivery, owner, concurrency, report, runtime tests
.github/workflows/ci.yml    PostgreSQL CI checks
docs/                       Deployment guide and SQL report evidence
```

## License

HookCourier is licensed under the [MIT License](LICENSE).
Third-party dependencies retain their own licenses.
