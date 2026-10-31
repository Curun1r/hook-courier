import csv
from datetime import UTC, date, datetime, timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.urls import reverse
from django.utils import timezone

from deliveries import delivery
from deliveries.models import DeliveryAttempt, Destination, Event
from deliveries.reporting import daily_delivery_report, daily_report_query


@pytest.fixture
def event(db):
    destination = Destination.objects.create(
        name="Report receiver", url="https://example.com/webhook"
    )
    return Event.objects.create(
        destination=destination,
        event_type="report.test",
        payload={},
        request_hash="0" * 64,
    )


@pytest.mark.parametrize(
    ("http_result", "previous_attempt", "expected_status"),
    [
        (delivery.HTTPResult(200), None, Event.Status.DELIVERED),
        (delivery.HTTPResult(400, "HTTP 400"), None, Event.Status.FAILED),
        (delivery.HTTPResult(503, "HTTP 503", True), 4, Event.Status.FAILED),
        (delivery.HTTPResult(503, "HTTP 503", True), None, Event.Status.PENDING),
    ],
)
def test_completion_timestamp_tracks_terminal_transition(
    event, http_result, previous_attempt, expected_status
):
    if previous_attempt is not None:
        DeliveryAttempt.objects.create(
            event=event,
            number=previous_attempt,
            status_code=503,
            duration_ms=1,
            error="HTTP 503",
        )
    before = timezone.now()
    with patch("deliveries.delivery.send_webhook", return_value=http_result):
        delivery.perform_delivery(str(event.pk))
    after = timezone.now()
    event.refresh_from_db()

    assert event.status == expected_status
    if expected_status == Event.Status.PENDING:
        assert event.completed_at is None
    else:
        assert before <= event.completed_at <= after
        completed_at = event.completed_at
        delivery.perform_delivery(str(event.pk))
        event.refresh_from_db()
        assert event.completed_at == completed_at


def test_exhausted_pending_event_gets_completion_timestamp_without_another_request(
    event, settings
):
    DeliveryAttempt.objects.create(
        event=event,
        number=settings.WEBHOOK_MAX_ATTEMPTS,
        status_code=503,
        duration_ms=1,
        error="HTTP 503",
    )
    before = timezone.now()
    with patch("deliveries.delivery.send_webhook") as send:
        delivery.perform_delivery(str(event.pk))
    event.refresh_from_db()

    send.assert_not_called()
    assert event.status == Event.Status.FAILED
    assert before <= event.completed_at <= timezone.now()


def test_admin_cannot_change_completion_timestamp(event, admin_client):
    completed_at = datetime(2026, 10, 24, 12, 30, tzinfo=UTC)
    Event.objects.filter(pk=event.pk).update(
        status=Event.Status.DELIVERED, completed_at=completed_at
    )
    response = admin_client.post(
        reverse("admin:deliveries_event_change", args=[event.pk]),
        {
            "completed_at_0": "2099-01-01",
            "completed_at_1": "00:00:00",
            "attempts-TOTAL_FORMS": "0",
            "attempts-INITIAL_FORMS": "0",
            "_save": "Save",
        },
    )

    assert response.status_code == 302
    event.refresh_from_db()
    assert event.completed_at == completed_at


@pytest.mark.django_db(transaction=True)
def test_migration_backfills_only_historical_terminal_events():
    previous = ("deliveries", "0002_event_next_attempt_at")
    latest = ("deliveries", "0003_event_completed_at_and_report_index")
    executor = MigrationExecutor(connection)
    executor.migrate([previous])
    try:
        old_apps = executor.loader.project_state([previous]).apps
        old_destination = old_apps.get_model("deliveries", "Destination")
        old_event = old_apps.get_model("deliveries", "Event")
        destination = old_destination.objects.create(
            name="Historical receiver", url="https://example.com/webhook"
        )
        modified_at = datetime(2026, 10, 20, 14, 30, tzinfo=UTC)
        event_ids = {}
        for status in ["delivered", "failed", "pending", "delivering"]:
            saved = old_event.objects.create(
                destination=destination,
                event_type="historical.test",
                payload={},
                request_hash="0" * 64,
                status=status,
            )
            old_event.objects.filter(pk=saved.pk).update(updated_at=modified_at)
            event_ids[status] = saved.pk
        executor = MigrationExecutor(connection)
        executor.migrate([latest])
        historical_event = executor.loader.project_state([latest]).apps.get_model(
            "deliveries", "Event"
        )
        for status, event_id in event_ids.items():
            saved = historical_event.objects.get(pk=event_id)
            expected = modified_at if status in ["delivered", "failed"] else None
            assert saved.completed_at == expected
    finally:
        MigrationExecutor(connection).migrate([("deliveries", "0004_delivery_leases")])


@pytest.mark.parametrize(
    ("from_date", "to_date", "destination_id", "message"),
    [
        (date(2026, 10, 25), date(2026, 10, 24), None, "from-date"),
        (date(2026, 10, 24), date(2026, 10, 24), 0, "destination-id"),
        (date(2026, 10, 24), date.max, None, "to-date"),
    ],
)
def test_report_rejects_invalid_bounds(from_date, to_date, destination_id, message):
    with pytest.raises(ValueError, match=message):
        daily_report_query(from_date, to_date, destination_id)


requires_postgresql = pytest.mark.skipif(
    connection.vendor != "postgresql", reason="The report uses PostgreSQL SQL"
)


@pytest.fixture
def report_data(db):
    first = Destination.objects.create(name="First", url="https://example.com/first")
    second = Destination.objects.create(name="Second", url="https://example.com/second")

    def create(destination, status, completed_at):
        return Event.objects.create(
            destination=destination,
            event_type="report.test",
            payload={},
            request_hash="0" * 64,
            status=status,
            completed_at=completed_at,
        )

    midnight = datetime(2026, 10, 24, tzinfo=UTC)
    create(first, Event.Status.DELIVERED, midnight - timedelta(microseconds=1))
    delivered = create(first, Event.Status.DELIVERED, midnight)
    create(first, Event.Status.FAILED, midnight + timedelta(hours=12))
    create(
        second, Event.Status.DELIVERED, midnight + timedelta(days=1, microseconds=-1)
    )
    create(first, Event.Status.FAILED, midnight + timedelta(days=1))
    create(first, Event.Status.PENDING, None)
    create(first, Event.Status.DELIVERING, None)
    Event.objects.filter(pk=delivered.pk).update(
        created_at=midnight - timedelta(days=5),
        updated_at=midnight + timedelta(days=5),
    )
    for number, status_code in [(1, 503), (2, 503), (3, 200)]:
        DeliveryAttempt.objects.create(
            event=delivered, number=number, status_code=status_code, duration_ms=1
        )
    return first, second


@pytest.mark.postgresql
@requires_postgresql
@pytest.mark.django_db
def test_report_counts_events_by_utc_completion_day(report_data):
    first, second = report_data
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL TIME ZONE 'Pacific/Honolulu'")
    rows = daily_delivery_report(date(2026, 10, 24), date(2026, 10, 25))

    assert [
        (row.destination_id, row.day, row.delivered, row.failed) for row in rows
    ] == [
        (first.pk, date(2026, 10, 24), 1, 1),
        (second.pk, date(2026, 10, 24), 1, 0),
        (first.pk, date(2026, 10, 25), 0, 1),
    ]


@pytest.mark.postgresql
@requires_postgresql
@pytest.mark.django_db
def test_report_command_filters_destination_and_prints_csv(report_data):
    first, _ = report_data
    output = StringIO()
    call_command(
        "delivery_report",
        "--from-date",
        "2026-10-24",
        "--to-date",
        "2026-10-24",
        "--destination-id",
        str(first.pk),
        stdout=output,
    )
    assert list(csv.reader(StringIO(output.getvalue()))) == [
        ["destination_id", "day", "delivered", "failed"],
        [str(first.pk), "2026-10-24", "1", "1"],
    ]


@pytest.mark.postgresql
@requires_postgresql
@pytest.mark.django_db
def test_report_empty_range_prints_only_csv_header():
    output = StringIO()
    call_command(
        "delivery_report",
        "--from-date",
        "2026-10-24",
        "--to-date",
        "2026-10-24",
        stdout=output,
    )
    assert output.getvalue().splitlines() == ["destination_id,day,delivered,failed"]


@pytest.mark.postgresql
@requires_postgresql
@pytest.mark.django_db
def test_report_explain_executes_the_real_report_query(report_data):
    output = StringIO()
    call_command(
        "delivery_report",
        "--from-date",
        "2026-10-24",
        "--to-date",
        "2026-10-24",
        "--explain",
        stdout=output,
    )
    plan = output.getvalue()
    assert "deliveries_event" in plan
    assert "Execution Time:" in plan
    assert "Buffers:" in plan


@pytest.mark.parametrize(
    "value", ["2026-02-30", "20261024", "2026-1-24", "x; DROP TABLE x"]
)
def test_report_command_rejects_invalid_dates(value):
    with pytest.raises(CommandError, match="YYYY-MM-DD"):
        call_command("delivery_report", "--from-date", value, "--to-date", "2026-10-24")
