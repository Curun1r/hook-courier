# Daily delivery report

The report counts completed **events**, grouped by destination ID and UTC completion
day. A delivered event with three HTTP attempts contributes one delivered event.
Pending and delivering events contribute nothing. Event creation time and the
time of the last update do not determine the report day.

## Completion time

`Event.completed_at` is written when delivery becomes `delivered` or `failed`,
including the defensive check that rejects an already exhausted pending event.
Temporary failures leave it empty. A duplicate task for a terminal event preserves
the original completion time.

Migration `0003_event_completed_at_and_report_index` initializes historical
terminal events from their existing `updated_at` values. This is an approximation:
the previous schema did not record completion separately. A later administrative
update to an old event may have moved that timestamp. Newly completed events use
the explicit completion time.

## Run the report

The report requires PostgreSQL. Both dates are required, inclusive, and interpreted
as UTC dates. The destination filter is optional.

```bash
docker compose exec web python src/manage.py delivery_report \
  --from-date 2026-10-01 --to-date 2026-10-24

docker compose exec web python src/manage.py delivery_report \
  --from-date 2026-10-24 --to-date 2026-10-24 --destination-id 2
```

Output is CSV, ordered by day and then destination ID:

```csv
destination_id,day,delivered,failed
2,2026-10-24,3,1
```

An empty range prints only the header. A destination with no terminal events in
the range has no row. Counts describe the current terminal event state; this is
not an immutable accounting ledger or a count of individual HTTP responses.

To save the output on the host:

```bash
docker compose exec -T web python src/manage.py delivery_report \
  --from-date 2026-10-01 --to-date 2026-10-24 > delivery-report.csv
```

## Query and index

The SQL in `src/deliveries/reporting.py` uses bound parameters for both date bounds
and the optional destination ID. The end bound is midnight after `--to-date`, so
an entire day is included without rounding timestamps. The UTC conversion in
`SELECT` makes grouping independent of the PostgreSQL session time zone.

```sql
SELECT destination_id,
       (completed_at AT TIME ZONE 'UTC')::date AS day,
       COUNT(*) FILTER (WHERE status = 'delivered') AS delivered,
       COUNT(*) FILTER (WHERE status = 'failed') AS failed
FROM deliveries_event
WHERE status IN ('delivered', 'failed')
  AND completed_at >= %s
  AND completed_at < %s
  -- Optional: AND destination_id = %s
GROUP BY destination_id, day
ORDER BY day, destination_id;
```

The migration creates this partial covering B-tree index:

```sql
CREATE INDEX event_terminal_report_idx
ON deliveries_event (completed_at, destination_id) INCLUDE (status)
WHERE status IN ('delivered', 'failed');
```

The leading completion timestamp supports the report's date range. Destination
ID is included as a second key for filtering and grouping, and the status is
available to the aggregate without retrieving payloads. Pending and delivering
rows are excluded. The existing `(status, created_at)` index serves the pending
recovery command and cannot narrow a completion-time range efficiently.

The terminal status predicate remains a fixed SQL expression matching the partial
index; user input never changes it. PostgreSQL must be able to infer the index
predicate from the query. A covering index permits an index-only scan, but recent
updates may still require heap visits until visibility information is available.
See [partial indexes](https://www.postgresql.org/docs/current/indexes-partial.html)
and [index-only scans](https://www.postgresql.org/docs/current/indexes-index-only-scans.html).

## Inspect the actual plan

```bash
docker compose exec web python src/manage.py delivery_report \
  --from-date 2026-10-24 --to-date 2026-10-24 --explain
```

`--explain` executes the same SELECT with `EXPLAIN (ANALYZE, BUFFERS)` and prints
the plan instead of CSV. It reads the selected data and measures that execution.
PostgreSQL remains free to choose a sequential scan for a small table or a broad
date range. The command does not force a particular plan.

## Disposable benchmark

The measured comparison is performed in a separate disposable database. The
application database and its events are not used for seeding, dropping indexes,
or running the comparison.

### Measured setup

Measured on 2026-10-24 with PostgreSQL 17.11 (Alpine, aarch64) in local Docker:

- Database: `hookcourier_benchmark`, separate from the application and test databases.
- 100 destinations and 100,000 events spread over 100 UTC days from 2026-01-01.
- 70,000 delivered, 10,000 failed, 10,000 pending, and 10,000 delivering events.
- JSON payload: a sequence number and 256 repeated padding characters.
- Rows inserted in completion-time order; no delivery attempts were needed.
- `VACUUM (ANALYZE) deliveries_event` ran after seeding.
- The exact SQL returned by `daily_report_query(date(2026, 2, 20), date(2026, 2, 20))`
  was executed with its bound UTC timestamps.
- The result contained 80 destination rows: 700 delivered and 100 failed events.
- Only `event_terminal_report_idx` was dropped and recreated between the two
  sets of runs. The existing primary-key, foreign-key, and recovery indexes stayed.
- The recreated index SQL came from the model index's Django `create_sql`, matching
  migration `0003`. Its measured size was 3,800 kB.
- No planner settings were changed and no scan method was forced.

### Results

Each value below is a complete `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` execution.
Buffer counts are taken from the top plan node and exclude planning buffers.

| Configuration | Execution times, three runs | Shared buffer hits | Shared buffer reads |
| --- | --- | --- | --- |
| Without report index | 21.360, 20.246, 19.987 ms | 6,253; 6,250; 6,250 | 0; 0; 0 |
| With report index | 0.450, 0.376, 0.385 ms | 1; 8; 8 | 7; 0; 0 |

The median execution time changed from 20.246 ms to 0.385 ms for this dataset.
Selected scan information from those measured JSON plans:

```text
Without report index, second run:
  GroupAggregate -> Sort -> Seq Scan on deliveries_event
  Seq Scan: actual rows=800, loops=1
  Rows Removed by Filter: 99200
  Buffers: shared hit=6250
  Execution Time: 20.246 ms

With report index, third run:
  GroupAggregate -> Sort -> Index Only Scan using event_terminal_report_idx
  Index Cond: completed_at >= 2026-02-20 00:00:00+00
              AND completed_at < 2026-02-21 00:00:00+00
  Index Only Scan: actual rows=800, loops=1
  Heap Fetches: 0
  Buffers: shared hit=8
  Execution Time: 0.385 ms
```

The index reduced the amount of data visited for a one-day selection containing
0.8% of all rows. These are local measurements, not a deployment latency promise.
The table was recently vacuumed, terminal rows were unchanged, and most data was
already cached. Continuous updates, larger payloads, different memory limits,
different destination distributions, and wider date ranges can produce different
plans and timings. The index also adds storage and work when an event completes.

### Reproduce the dataset

Create a fresh disposable PostgreSQL database, apply the project's migrations to
that database, and verify it contains no events. Run the SQL below there only:

```sql
INSERT INTO deliveries_destination
    (id, name, url, is_active, created_at, updated_at)
SELECT i, 'Benchmark receiver ' || i,
       'https://receiver.example/webhook/' || i, true, now(), now()
FROM generate_series(1, 100) AS i;

WITH seed AS (
    SELECT i,
           TIMESTAMPTZ '2026-01-01 00:00:00+00'
           + ((i - 1) / 1000) * INTERVAL '1 day'
           + ((i - 1) % 1000) * INTERVAL '86.4 seconds' AS finished,
           CASE WHEN i % 10 < 7 THEN 'delivered'
                WHEN i % 10 = 7 THEN 'failed'
                WHEN i % 10 = 8 THEN 'pending'
                ELSE 'delivering' END AS state
    FROM generate_series(1, 100000) AS i
)
INSERT INTO deliveries_event
    (id, destination_id, event_type, payload, idempotency_key, request_hash,
     status, next_attempt_at, completed_at, created_at, updated_at)
SELECT md5(i::text)::uuid, ((i - 1) % 100) + 1, 'benchmark.event',
       jsonb_build_object('sequence', i, 'padding', repeat('x', 256)),
       NULL, repeat('0', 64), state, NULL,
       CASE WHEN state IN ('delivered', 'failed') THEN finished ELSE NULL END,
       finished - INTERVAL '1 minute', finished
FROM seed;

VACUUM (ANALYZE) deliveries_event;
```

Use the command with `--from-date 2026-02-20 --to-date 2026-02-20 --explain` to
inspect the migrated index. For a comparison, in this disposable database only:
drop `event_terminal_report_idx`, run the same plan three times, recreate the
index using the SQL above, run `ANALYZE deliveries_event`, and run it three more
times. Keep the dates, SQL, remaining indexes, data, and planner settings unchanged.
Do not sum buffer counters from parent and child nodes: they include overlapping
work.
