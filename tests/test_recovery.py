import uuid
from datetime import timedelta
from io import StringIO
from unittest.mock import call, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from kombu.exceptions import OperationalError

from deliveries.models import DeliveryAttempt, Destination, Event

pytestmark = pytest.mark.django_db


@pytest.fixture
def make_event():
    destination = Destination.objects.create(
        name="Recovery receiver", url="https://example.com/webhook?token=secret"
    )

    def create(**overrides):
        return Event.objects.create(
            **(
                {
                    "destination": destination,
                    "event_type": "appointment.created",
                    "payload": {"appointment_id": 123},
                    "request_hash": "0" * 64,
                }
                | overrides
            )
        )

    return create


def test_only_due_pending_events_are_queued(make_event, broker_publish):
    now = timezone.now()
    unscheduled = make_event()
    overdue = make_event(next_attempt_at=now - timedelta(seconds=1))
    due_now = make_event(next_attempt_at=now)
    make_event(next_attempt_at=now + timedelta(seconds=1))
    for status in (
        Event.Status.DELIVERING,
        Event.Status.DELIVERED,
        Event.Status.FAILED,
    ):
        make_event(status=status)
    before = list(Event.objects.order_by("id").values())

    with patch(
        "deliveries.management.commands.requeue_pending.timezone.now", return_value=now
    ):
        output = StringIO()
        call_command("requeue_pending", stdout=output)

    assert broker_publish.call_args_list == [
        call(args=[str(event.pk)]) for event in (unscheduled, overdue, due_now)
    ]
    assert "Queued 3 due pending event(s)" in output.getvalue()
    assert list(Event.objects.order_by("id").values()) == before
    assert DeliveryAttempt.objects.count() == 0


def test_limit_uses_oldest_events_and_stable_id_order(make_event, broker_publish):
    newest = make_event(id=uuid.UUID(int=1))
    older_high_id = make_event(id=uuid.UUID(int=3))
    older_low_id = make_event(id=uuid.UUID(int=2))
    old_time = timezone.now() - timedelta(days=1)
    Event.objects.filter(pk__in=[older_high_id.pk, older_low_id.pk]).update(
        created_at=old_time
    )

    call_command("requeue_pending", "--limit", "2", stdout=StringIO())

    assert broker_publish.call_args_list == [
        call(args=[str(older_low_id.pk)]),
        call(args=[str(older_high_id.pk)]),
    ]
    newest.refresh_from_db()
    assert newest.status == Event.Status.PENDING


def test_default_limit_is_100(make_event, broker_publish):
    first = make_event()
    Event.objects.bulk_create(
        [
            Event(
                destination=first.destination,
                event_type="appointment.created",
                payload={},
                request_hash="0" * 64,
            )
            for _ in range(100)
        ]
    )

    call_command("requeue_pending", stdout=StringIO())

    assert broker_publish.call_count == 100


def test_dry_run_counts_within_limit_and_does_not_publish(make_event, broker_publish):
    make_event()
    make_event()
    before = list(Event.objects.order_by("id").values())
    output = StringIO()

    call_command("requeue_pending", "--limit", "1", "--dry-run", stdout=output)

    assert "Dry run: 1 due pending event(s) would be queued (limit=1)." in (
        output.getvalue()
    )
    broker_publish.assert_not_called()
    assert list(Event.objects.order_by("id").values()) == before
    assert DeliveryAttempt.objects.count() == 0


def test_empty_queue_succeeds_without_publishing(broker_publish):
    output = StringIO()

    call_command("requeue_pending", stdout=output)

    assert "Queued 0 due pending event(s)" in output.getvalue()
    broker_publish.assert_not_called()


@pytest.mark.parametrize("limit", ["0", "-1", "invalid"])
def test_invalid_limit_is_rejected(limit, broker_publish):
    with pytest.raises(CommandError, match="positive integer"):
        call_command("requeue_pending", "--limit", limit, stdout=StringIO())
    broker_publish.assert_not_called()


def test_broker_failure_stops_with_nonzero_error_and_keeps_data(
    make_event, broker_publish, caplog
):
    for _ in range(3):
        make_event()
    before = list(Event.objects.order_by("id").values())
    broker_publish.side_effect = [
        None,
        OperationalError("redis://private:secret-password@example.com/0"),
        None,
    ]

    with pytest.raises(CommandError, match="after queuing 1 event") as error:
        call_command("requeue_pending", stdout=StringIO())

    assert error.value.returncode == 1
    assert "Restore Redis" in str(error.value)
    assert "secret-password" not in str(error.value)
    assert "secret-password" not in caplog.text
    assert broker_publish.call_count == 2
    assert list(Event.objects.order_by("id").values()) == before
    assert DeliveryAttempt.objects.count() == 0
