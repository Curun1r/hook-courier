# Week 2 — Background delivery (18–20 hours)

## 1. Redis and Celery — 3 hours

- Configure Redis as the broker and add a separate Celery worker to Compose.
- Publish a task after the event transaction commits.
- Keep accepted events pending if publishing fails.
- Verify that rollback and idempotent replay do not publish a new task.

## 2. HTTP delivery and history — 5 hours

- Send an event envelope with its immutable ID, type, and payload.
- Record the number, timestamp, HTTP status, duration, and safe error for every attempt.
- Claim an event in a database transaction so duplicate jobs cannot send it concurrently.
- Limit the outgoing body to 64 KiB and configure a five-second timeout per HTTP I/O phase.

## 3. Retry policy — 4 hours

- Retry network errors, timeouts, HTTP 429, and HTTP 5xx.
- Treat other HTTP 4xx and redirects as final failures.
- Allow five attempts in total, with exponential delays of 2, 4, 8, and 16 seconds by default.
- Store the next attempt time and enforce both the time and attempt limit in PostgreSQL.

## 4. Demo receiver — 3 hours

- Add an internal receiver that returns 503 twice per event ID and 200 on the third request.
- Display the destination ID in Admin so the example request is easy to prepare.
- Verify the full API → PostgreSQL → Redis → worker → receiver path.

## 5. Verification and documentation — 3–5 hours

- Test successful delivery, temporary and permanent errors, timeout, exhausted attempts, duplicate jobs, body size, and broker failure.
- Document delivery semantics, configuration, and the reproducible demo.
- Verify migrations, Django checks, formatting, and the Compose demo with PostgreSQL.

## Acceptance criteria

A new event is accepted with 202, delivered in the worker, and reaches delivered after exactly three recorded demo attempts: 503, 503, 200. A replay returns the same event without scheduling another delivery. When Redis is unavailable, the API preserves the event as pending.

## Verification completed

- 43 automated tests passed, including the original API tests.
- Django checks, migration consistency, Ruff checks, and formatting passed.
- The running PostgreSQL/Redis/Celery demo recorded 503, 503, 200 and reached delivered.
- Two extra queued jobs for the same event did not produce extra delivery attempts.
- With Redis stopped, the API returned 202 and retained a pending event with no attempts. Manually requeueing it after Redis restarted delivered it successfully.

## Next milestone

Week three adds the status page, pending-event recovery command, CI, reporting query, deployment setup, and final error checks. Worker interruption after claiming an event still requires manual attention; crash recovery is not implemented in this milestone.

## Git checkpoints

This milestone uses separate commits for the Celery runtime, delivery and retries, API queue publication, the demo receiver, and documentation. Intermediate code snapshots passed their applicable tests before each feature commit. See [version control](version-control.md) for commit IDs and instructions for inspecting earlier code.
