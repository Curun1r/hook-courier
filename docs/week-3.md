# Week 3 — Recovery and owner dashboard

## Completed in this change

### 1. Safe HTTP client logging

HTTPX can log complete receiver URLs when the Celery root logger runs at INFO. Django logging now prevents HTTPX and HTTPCore messages from propagating to application handlers. Safe application warnings still include the event ID and omit exception messages that could contain credentials.

Regression tests exercise real HTTPX requests through a mock transport for both successful and failed responses, using a fake URL password and query token. The tests verify delivery still happens and those values do not reach captured logs.

### 2. Pending-event recovery

```bash
docker compose exec web python src/manage.py requeue_pending --dry-run
docker compose exec web python src/manage.py requeue_pending --limit 100
```

The command selects at most 100 due pending events by default, ordered by creation time and ID. Scheduled retries in the future, delivering events, and terminal states are excluded. Dry-run counts the selected batch without accessing Redis. A broker failure stops publication and returns a nonzero exit status with a safe explanation.

Publishing a job does not change an event's state or erase attempts. Repeated invocations can publish duplicate jobs; the existing worker uses a database lock and the saved status to claim a pending event. The command does not attempt to recover an interrupted worker's delivering event.

### 3. Owner dashboard

- `/`: event list, status filter, attempt counts, and 25-item pagination.
- `/events/UUID/`: event information, formatted payload, next retry time, and ordered attempts.
- Existing Django Admin login and logout; access requires an active superuser.
- Anonymous users are redirected to login; authenticated non-owners receive 403.
- The API key and owner session remain separate authentication mechanisms.
- Templates escape payloads, names, and errors; sensitive receiver URLs are not displayed.
- Owner responses have private/no-store cache headers.
- Refresh buttons update the page without a separate frontend or polling service.

Templates and CSS are included in the Python package as well as the Docker source tree.

## Verification

- 75 automated test cases pass: 43 existing cases, 3 logging cases, 9 recovery cases, and 20 dashboard cases.
- Tests cover access boundaries, inactive accounts, pagination, filtering, escaped HTML payloads, caching, recovery eligibility, and broker failure.
- Ruff and Django checks pass; no database migration is required for these changes.
- The Docker integration check uses separate PostgreSQL and Redis volumes. It accepts an event during a Redis outage, restores Redis, invokes recovery twice, and verifies exactly three attempts: 503, 503, 200.
- Celery worker logs are checked for the absence of a fake secret URL parameter at INFO.
- Installed package metadata is checked for all three dashboard templates and the stylesheet.

## Remaining first-version work

1. GitHub Actions, including a PostgreSQL test job and concurrency checks.
2. Delivery report by destination and day, with an execution plan and justified index.
3. Deployment configuration, hosting selection, and final reproducibility checks.
4. Final documentation and documented handling of interrupted delivering events.

Public registration, multiple organizations, extra delivery channels, and a complex frontend remain outside the first version.
