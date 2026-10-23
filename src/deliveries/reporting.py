from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from django.db import NotSupportedError, connection


@dataclass(frozen=True)
class DailyDeliveryCount:
    destination_id: int
    day: date
    delivered: int
    failed: int


def daily_report_query(
    from_date: date, to_date: date, destination_id: int | None = None
) -> tuple[str, list]:
    if from_date > to_date:
        raise ValueError("from-date must be on or before to-date")
    if destination_id is not None and destination_id < 1:
        raise ValueError("destination-id must be a positive integer")
    try:
        exclusive_end = to_date + timedelta(days=1)
    except OverflowError as exc:
        raise ValueError("to-date must be earlier than 9999-12-31") from exc

    # Keep terminal statuses literal so the planner can match the partial index.
    sql = """
        SELECT destination_id,
               (completed_at AT TIME ZONE 'UTC')::date AS day,
               COUNT(*) FILTER (WHERE status = 'delivered') AS delivered,
               COUNT(*) FILTER (WHERE status = 'failed') AS failed
        FROM deliveries_event
        WHERE status IN ('delivered', 'failed')
          AND completed_at >= %s
          AND completed_at < %s
    """
    params = [
        datetime.combine(from_date, time.min, tzinfo=UTC),
        datetime.combine(exclusive_end, time.min, tzinfo=UTC),
    ]
    if destination_id is not None:
        sql += " AND destination_id = %s"
        params.append(destination_id)
    sql += " GROUP BY destination_id, day ORDER BY day, destination_id"
    return sql, params


def daily_delivery_report(
    from_date: date, to_date: date, destination_id: int | None = None
) -> list[DailyDeliveryCount]:
    if connection.vendor != "postgresql":
        raise NotSupportedError("The delivery report requires PostgreSQL")
    sql, params = daily_report_query(from_date, to_date, destination_id)
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return [DailyDeliveryCount(*row) for row in cursor.fetchall()]


def explain_daily_report(
    from_date: date, to_date: date, destination_id: int | None = None
) -> str:
    if connection.vendor != "postgresql":
        raise NotSupportedError("The delivery report requires PostgreSQL")
    sql, params = daily_report_query(from_date, to_date, destination_id)
    with connection.cursor() as cursor:
        cursor.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql, params)
        return "\n".join(row[0] for row in cursor.fetchall())
