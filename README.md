# HookCourier

HookCourier accepts JSON events and delivers them to an owner-configured HTTP destination in a background worker. PostgreSQL stores events and attempt history; Redis carries Celery jobs.

```text
Sender → Django API → PostgreSQL → Redis → Celery worker → HTTP receiver
                          ↑                    │
                          └── attempt history ─┘
```

## Milestones

- [Week one: service foundation](docs/week-1.md)
- [Week two: background delivery](docs/week-2.md)
- [Week three: recovery and owner dashboard](docs/week-3.md) (in progress)

See [Git checkpoints and returning to earlier code](docs/version-control.md) for the commit history and workflow.

## Repository structure

```text
src/config/                  Django and Celery configuration
src/deliveries/              Models, Admin, API, queueing, and delivery rules
src/deliveries/migrations/   Database migrations
src/deliveries/management/   Pending-event recovery command
src/deliveries/templates/    Owner dashboard pages
src/deliveries/static/       Dashboard stylesheet
demo/receiver.py             Receiver that fails twice, then succeeds
tests/                       API, transaction, delivery, and retry tests
docs/                        Milestone plans
```

## Run locally

1. Copy `.env.example` to `.env` and replace every `change-this-...` value with your own secret. Keep `.env` out of Git. If a local `.env` already exists, retain its secrets; optional delivery settings have defaults.
2. Start Docker Desktop, then run `docker compose up --build -d`.
3. Check the services with `docker compose ps`. Compose runs the migrations before the worker starts.
4. Create the owner account if needed: `docker compose exec web python src/manage.py createsuperuser`.
5. Sign in to [Django Admin](http://localhost:8000/admin/).
6. Open the [owner dashboard](http://localhost:8000/) with that account. Access requires an active superuser; an API key alone does not grant access to these pages.

The API listens on port 8000. PostgreSQL, Redis, and the receiver are available only on the Compose network. This setup uses Django's development server for local work.

## Demo: two failures, then success

In Admin, create an active Destination with these values:

- Name: `Retry demo receiver`
- URL: `http://receiver.local:9000/webhook/`

The destination ID appears in the Admin list and edit form. Use that number as `destination_id` below. The receiver tracks attempts separately for each immutable event ID and returns 503, 503, then 200. Its counters reset when its container restarts.

From the repository directory, load the local key and send an event:

```bash
source .env
curl -i http://localhost:8000/api/v1/events/ \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $HOOKCOURIER_API_KEY" \
  -H 'Idempotency-Key: demo-retries-001' \
  -d '{"destination_id":1,"event_type":"appointment.created","payload":{"appointment_id":123}}'
```

The API returns `202 Accepted`, the event ID, and a `Location` header. Replace `EVENT_UUID` with that ID to inspect delivery:

```bash
curl -s http://localhost:8000/api/v1/events/EVENT_UUID/ \
  -H "X-API-Key: $HOOKCOURIER_API_KEY" | python3 -m json.tool
```

After about six seconds plus request time, the event should be `delivered` with three attempts whose HTTP codes are `503`, `503`, and `200`. The same history is visible in the owner dashboard and Admin → Events. Use the dashboard's Refresh button to see updates. Inspect the processes with `docker compose logs worker receiver`.

Repeating a request with the same idempotency key and event data returns the same event without scheduling another delivery. Reusing the key with different data returns `409 Conflict`. Use a new key for each new demo run. Omitting the key creates a new event for every request.

## Delivery behavior

| State | Meaning |
|---|---|
| `pending` | Waiting for the first attempt, a scheduled retry, or queue recovery |
| `delivering` | A worker has claimed the event and is making the request |
| `delivered` | The receiver returned an HTTP 2xx response |
| `failed` | A permanent error occurred or the attempt limit was reached |

- HTTP 2xx succeeds.
- Network errors, timeouts, HTTP 429, and HTTP 5xx are retried.
- Other HTTP 4xx and HTTP redirects fail immediately. Redirects are not followed.
- The default limit is five attempts, including the first request. Delays are 2, 4, 8, and 16 seconds.
- The outgoing JSON body is limited to 65,536 bytes. Oversized events are rejected by the API; the worker also checks saved events.
- HTTPX uses a five-second timeout for each I/O phase. Response bodies are not read or stored.
- A destination disabled before delivery produces a failed attempt without an HTTP request.

Each webhook contains `id`, `event_type`, and `payload`. The `X-HookCourier-Event-ID` header repeats the event ID. The sender's API key is never forwarded to the receiver.

Delivery can be repeated when a receiver processes a request but its response is lost. HookCourier does not promise exactly one delivery. Receivers should use the event ID to recognize duplicates.

## Queue failure and current limitations

Jobs are published with Django's `transaction.on_commit()`. A broker failure does not undo the saved event or turn the accepted request into an error: the event remains pending. Publishing failures are logged using only the event ID.

Preview the due pending events, then queue them after Redis is available:

```bash
docker compose exec web python src/manage.py requeue_pending --dry-run
docker compose exec web python src/manage.py requeue_pending --limit 100
```

The default limit is 100 events, ordered oldest first. Only `pending` events with no scheduled time or with a time that has already arrived are selected. Future retries and other states are skipped. Dry-run does not publish jobs. A publishing failure stops the command with a nonzero exit status; restore Redis and run it again. Existing attempt history and delivery states are retained. Duplicate jobs remain safe under the worker's database claim and status checks.

This also applies to events accepted before the worker was added. An interrupted worker can leave an event in `delivering`; the recovery command skips that state, and automatic crash recovery is not implemented yet.

Destinations are managed by the trusted owner in Admin. The API does not accept arbitrary receiver URLs. Opening destination management to untrusted users requires SSRF protection before deployment.

## Owner dashboard

- `/` lists events newest first, with destination, status, attempt count, and creation time.
- Filter by status and browse 25 events per page.
- `/events/EVENT_UUID/` shows the JSON payload, next retry time, and complete attempt history.
- Anonymous visitors are redirected to the existing Admin login. Authenticated users without superuser status receive 403.
- Payloads and error text are escaped, and owner pages are marked private and non-cacheable. All times are UTC.
- Dashboard authentication uses the owner session; the JSON API continues to require `X-API-Key`.

## Configuration

Compose sets the broker URL to `redis://redis:6379/0`. Optional settings in `.env` override these defaults:

| Setting | Default |
|---|---|
| `WEBHOOK_MAX_ATTEMPTS` | `5` |
| `WEBHOOK_TIMEOUT_SECONDS` | `5` |
| `WEBHOOK_MAX_BYTES` | `65536` |
| `WEBHOOK_RETRY_BASE_SECONDS` | `2` |
| `WEBHOOK_RETRY_MAX_SECONDS` | `60` |

Changes require recreating the web and worker containers, for example with `docker compose up -d --force-recreate web worker`. API secrets, event payloads, response bodies, and destination credentials are excluded from delivery logs; HTTP errors store a status code or exception class. HTTPX and HTTPCore logs are suppressed because their request messages can include secret URL parameters. Application warnings remain available.

## Run checks

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
DB_ENGINE=sqlite DJANGO_DEBUG=1 .venv/bin/python -m pytest
DB_ENGINE=sqlite DJANGO_DEBUG=1 .venv/bin/python src/manage.py check
DB_ENGINE=sqlite DJANGO_DEBUG=1 .venv/bin/python src/manage.py makemigrations --check --dry-run
.venv/bin/ruff check src tests demo
.venv/bin/ruff format --check src tests demo
```

Local unit tests use SQLite, a mocked broker, and an HTTPX mock receiver. The Compose demo verifies the running Redis broker, Celery worker, and PostgreSQL database. CI and a PostgreSQL test job are planned for week three.
