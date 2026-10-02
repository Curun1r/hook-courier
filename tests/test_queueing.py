from unittest.mock import patch

import pytest
from celery.exceptions import Reject, Retry
from django.db import transaction
from kombu.exceptions import OperationalError
from rest_framework.test import APIClient

from deliveries.delivery import DeliveryOutcome
from deliveries.models import Destination, Event
from deliveries.services import accept_event
from deliveries.tasks import deliver_event

pytestmark = pytest.mark.django_db


@pytest.fixture
def event_input():
    destination = Destination.objects.create(
        name="Receiver", url="https://example.com/webhook"
    )
    return {
        "destination_id": destination.pk,
        "event_type": "appointment.created",
        "payload": {"appointment_id": 123},
    }


def test_publish_runs_only_after_commit(
    event_input, broker_publish, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        event, replayed = accept_event(**event_input, idempotency_key="new-event")
        assert replayed is False
        broker_publish.assert_not_called()
    assert len(callbacks) == 1
    broker_publish.assert_called_once_with(args=[str(event.pk)])


def test_replay_does_not_publish_again(
    event_input, broker_publish, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        event, _ = accept_event(**event_input, idempotency_key="same-event")
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        replay, replayed = accept_event(**event_input, idempotency_key="same-event")
    assert replayed is True
    assert replay.pk == event.pk
    assert callbacks == []
    broker_publish.assert_called_once()


def test_rollback_does_not_publish(
    event_input, broker_publish, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                accept_event(**event_input, idempotency_key="rolled-back")
                raise RuntimeError("Abort the transaction")
    assert callbacks == []
    assert Event.objects.count() == 0
    broker_publish.assert_not_called()


def test_broker_failure_keeps_event_and_returns_202(
    event_input, broker_publish, settings, django_capture_on_commit_callbacks, caplog
):
    settings.HOOKCOURIER_API_KEY = "test-api-key"
    broker_publish.side_effect = OperationalError("secret must not be logged")
    with django_capture_on_commit_callbacks(execute=True):
        response = APIClient().post(
            "/api/v1/events/", event_input, format="json", HTTP_X_API_KEY="test-api-key"
        )
    assert response.status_code == 202
    assert Event.objects.get(pk=response.data["id"]).status == Event.Status.PENDING
    assert "could not be queued" in caplog.text
    assert "secret must not be logged" not in caplog.text


def test_task_schedules_celery_retry():
    with patch("deliveries.tasks.perform_delivery", return_value=DeliveryOutcome(4)):
        with patch.object(deliver_event, "retry", side_effect=Retry()) as retry:
            with pytest.raises(Retry):
                deliver_event.run("test-event-id")
    retry.assert_called_once_with(countdown=4, max_retries=None)


def test_retry_publish_failure_is_logged_without_sensitive_details(caplog):
    with patch("deliveries.tasks.perform_delivery", return_value=DeliveryOutcome(2)):
        with patch.object(
            deliver_event, "retry", side_effect=Reject(OperationalError("secret URL"))
        ):
            deliver_event.run("test-event-id")
    assert "could not be queued" in caplog.text
    assert "secret URL" not in caplog.text
