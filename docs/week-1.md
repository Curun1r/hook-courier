# Week 1 — Service foundation (18–20 hours)

## 1. Repository and local startup — 3 hours

- Initialize Git and add `.gitignore`, `pyproject.toml`, `.env.example`, `Dockerfile`, and `compose.yaml`.
- Start the Django API and PostgreSQL with Compose.
- Verify with `docker compose config`, `docker compose up --build`, and `python src/manage.py check`.

## 2. Data model and Admin — 4 hours

- Add `Destination`, `Event`, and `DeliveryAttempt` with an initial migration.
- Restrict destination management to Django Admin.
- Verify that migrations apply and an active destination can be created in Admin.

## 3. API contract and key — 3 hours

- Protect `POST /api/v1/events/` and `GET /api/v1/events/{id}/` with `X-API-Key`.
- Validate the event type, JSON object, and active destination.
- Verify that requests without a valid key are denied and invalid input returns `400`.

## 4. Event acceptance and idempotency — 5 hours

- Store events in a transaction and return `202 Accepted` with an immutable UUID.
- Canonicalize request data for its fingerprint and enforce key uniqueness in the database.
- Return the same event for a matching replay; return `409` when the same key carries different data.
- Verify replay, conflict, and invalid destination behavior with automated tests.

## 5. Tests and documentation — 3–5 hours

- Check the API, migrations, configuration, and example requests.
- Document the current `pending` behavior and the next milestone for queueing.
- Verify that tests and `manage.py check` pass and the startup steps work.

## Scope of this milestone

`DeliveryAttempt` is part of the schema, but this milestone does not send HTTP requests. Redis, Celery, retries, and the demo receiver belong to week two. The status page, recovery command, and CI belong to week three.

Week-one acceptance criteria: the owner can create an active destination in Admin; the keyed API stores an event in PostgreSQL; replays and conflicts behave as documented; tests pass.

