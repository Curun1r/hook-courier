# HookCourier

HookCourier is a small HTTP webhook relay. The first milestone stores events in PostgreSQL, protects the API with a key, and supports idempotent requests. Delivery through Redis and Celery is planned for week two.

## Project plan

See [the week-one plan](docs/week-1.md) for concrete tasks and acceptance criteria.

## Repository structure

```text
src/config/                  Django settings and URL routing
src/deliveries/              Models, Admin, API, and business rules
src/deliveries/migrations/   PostgreSQL migrations
tests/                       API and event acceptance tests
docs/                        Milestone plans
```

The demo receiver, Celery worker, status page, and CI workflow will be added in their planned milestones.

## Run locally

1. Copy `.env.example` to `.env` and replace every `change-this-...` value with your own secret. Keep `.env` out of Git.
2. Run `docker compose up --build -d`.
3. Create the owner account: `docker compose exec web python src/manage.py createsuperuser`.
4. Sign in to [Django Admin](http://localhost:8000/admin/) and create an active `Destination`.

After creating a destination, send an event:

```bash
curl -i http://localhost:8000/api/v1/events/ \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -H 'Idempotency-Key: appointment-123' \
  -d '{"destination_id":1,"event_type":"appointment.created","payload":{"appointment_id":123}}'
```

The API returns `202 Accepted`, an event ID, and the `pending` status. Repeating the request with the same idempotency key and data returns the same event. Reusing the key with different data returns `409 Conflict`. Read an event at `GET /api/v1/events/{id}/` with the same API key. Until the worker is added, events remain `pending`.

## Run tests

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
DB_ENGINE=sqlite DJANGO_DEBUG=1 .venv/bin/pytest
```

SQLite is used for fast local tests; the application runs with PostgreSQL. Check for missing migrations with `DB_ENGINE=sqlite DJANGO_DEBUG=1 .venv/bin/python src/manage.py makemigrations --check --dry-run`.

