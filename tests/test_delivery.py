import json
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from deliveries import delivery
from deliveries.models import Destination, Event

pytestmark = pytest.mark.django_db


@pytest.fixture
def event():
    destination = Destination.objects.create(
        name="Receiver", url="https://example.com/webhook"
    )
    return Event.objects.create(
        destination=destination,
        event_type="appointment.created",
        payload={"appointment_id": 123},
        request_hash="0" * 64,
    )


@pytest.fixture
def http_receiver(monkeypatch):
    original_client = httpx.AsyncClient
    requests = []
    options = []

    def install(handler):
        def receive(request):
            requests.append(request)
            return handler(request)

        def client(**kwargs):
            options.append(kwargs)
            return original_client(transport=httpx.MockTransport(receive), **kwargs)

        monkeypatch.setattr(delivery.httpx, "AsyncClient", client)
        return requests, options

    return install


@pytest.mark.parametrize("status_code", [200, 201, 204])
def test_success_records_attempt_and_marks_delivered(event, http_receiver, status_code):
    requests, options = http_receiver(lambda request: httpx.Response(status_code))
    outcome = delivery.perform_delivery(str(event.pk))
    event.refresh_from_db()
    attempt = event.attempts.get()
    assert event.status == Event.Status.DELIVERED
    assert outcome.retry_after is None
    assert attempt.number == 1
    assert attempt.status_code == status_code
    assert attempt.duration_ms >= 0
    assert attempt.error == ""
    assert json.loads(requests[0].content) == {
        "id": str(event.pk),
        "event_type": event.event_type,
        "payload": event.payload,
    }
    assert requests[0].headers["X-HookCourier-Event-ID"] == str(event.pk)
    assert "X-API-Key" not in requests[0].headers
    assert options[0]["timeout"] == 5
    assert options[0]["follow_redirects"] is False
    assert options[0]["trust_env"] is False


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422, 302, 307])
def test_permanent_http_error_does_not_retry(event, http_receiver, status_code):
    requests, _ = http_receiver(
        lambda request: httpx.Response(
            status_code, headers={"Location": "https://other.example.com/"}
        )
    )
    outcome = delivery.perform_delivery(str(event.pk))
    event.refresh_from_db()
    assert event.status == Event.Status.FAILED
    assert outcome.retry_after is None
    assert event.attempts.get().error == f"HTTP {status_code}"
    assert len(requests) == 1


@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
def test_temporary_http_error_schedules_retry(event, http_receiver, status_code):
    http_receiver(lambda request: httpx.Response(status_code))
    outcome = delivery.perform_delivery(str(event.pk))
    event.refresh_from_db()
    assert event.status == Event.Status.PENDING
    assert event.next_attempt_at > timezone.now()
    assert outcome.retry_after == 2
    assert event.attempts.get().status_code == status_code


@pytest.mark.parametrize("exception_type", [httpx.ConnectError, httpx.ReadTimeout])
def test_network_errors_are_recorded_without_leaking_details(
    event, http_receiver, exception_type
):
    def fail(request):
        raise exception_type("secret URL or credentials", request=request)

    http_receiver(fail)
    outcome = delivery.perform_delivery(str(event.pk))
    event.refresh_from_db()
    attempt = event.attempts.get()
    assert event.status == Event.Status.PENDING
    assert outcome.retry_after == 2
    assert attempt.status_code is None
    assert attempt.error == exception_type.__name__


def test_two_failures_then_success_keep_same_id_and_three_attempts(
    event, http_receiver
):
    statuses = iter([503, 503, 200])
    requests, _ = http_receiver(lambda request: httpx.Response(next(statuses)))
    delays = []
    for _ in range(3):
        Event.objects.filter(pk=event.pk).update(next_attempt_at=None)
        delays.append(delivery.perform_delivery(str(event.pk)).retry_after)
    event.refresh_from_db()
    assert delays == [2, 4, None]
    assert event.status == Event.Status.DELIVERED
    assert list(event.attempts.values_list("number", "status_code")) == [
        (1, 503),
        (2, 503),
        (3, 200),
    ]
    assert {request.headers["X-HookCourier-Event-ID"] for request in requests} == {
        str(event.pk)
    }
    assert {request.content for request in requests} == {requests[0].content}


def test_attempt_limit_is_enforced_across_task_runs(event, http_receiver, settings):
    settings.WEBHOOK_MAX_ATTEMPTS = 3
    requests, _ = http_receiver(lambda request: httpx.Response(503))
    for _ in range(3):
        Event.objects.filter(pk=event.pk).update(next_attempt_at=None)
        outcome = delivery.perform_delivery(str(event.pk))
    event.refresh_from_db()
    assert event.status == Event.Status.FAILED
    assert event.next_attempt_at is None
    assert outcome.retry_after is None
    delivery.perform_delivery(str(event.pk))
    assert len(requests) == event.attempts.count() == 3


@pytest.mark.parametrize(
    "state", [Event.Status.DELIVERED, Event.Status.FAILED, Event.Status.DELIVERING]
)
def test_duplicate_task_does_not_send_again(event, http_receiver, state):
    requests, _ = http_receiver(lambda request: httpx.Response(200))
    Event.objects.filter(pk=event.pk).update(status=state)
    assert delivery.perform_delivery(str(event.pk)).retry_after is None
    assert requests == []
    assert event.attempts.count() == 0


def test_early_task_respects_scheduled_retry_time(event, http_receiver):
    requests, _ = http_receiver(lambda request: httpx.Response(200))
    Event.objects.filter(pk=event.pk).update(
        next_attempt_at=timezone.now() + timedelta(seconds=30)
    )
    outcome = delivery.perform_delivery(str(event.pk))
    assert 29 <= outcome.retry_after <= 30
    assert requests == []
    assert event.attempts.count() == 0


def test_disabled_destination_fails_without_network_request(event, http_receiver):
    requests, _ = http_receiver(lambda request: httpx.Response(200))
    event.destination.is_active = False
    event.destination.save()
    delivery.perform_delivery(str(event.pk))
    event.refresh_from_db()
    assert event.status == Event.Status.FAILED
    assert event.attempts.get().error == "Destination is inactive"
    assert requests == []


def test_worker_rejects_oversized_saved_event(event, http_receiver, settings):
    settings.WEBHOOK_MAX_BYTES = 100
    requests, _ = http_receiver(lambda request: httpx.Response(200))
    Event.objects.filter(pk=event.pk).update(payload={"data": "x" * 200})
    delivery.perform_delivery(str(event.pk))
    event.refresh_from_db()
    assert event.status == Event.Status.FAILED
    assert "size limit" in event.attempts.get().error
    assert requests == []


def test_api_rejects_oversized_utf8_payload(event, settings):
    settings.HOOKCOURIER_API_KEY = "test-api-key"
    settings.WEBHOOK_MAX_BYTES = 150
    response = APIClient().post(
        "/api/v1/events/",
        {
            "destination_id": event.destination_id,
            "event_type": event.event_type,
            "payload": {"data": "界" * 100},
        },
        format="json",
        HTTP_X_API_KEY="test-api-key",
    )
    assert response.status_code == 400
    assert Event.objects.count() == 1


def test_missing_event_is_ignored(http_receiver):
    requests, _ = http_receiver(lambda request: httpx.Response(200))
    delivery.perform_delivery(str(uuid4()))
    assert requests == []


def test_exponential_backoff_is_capped(settings):
    settings.WEBHOOK_RETRY_BASE_SECONDS = 2
    settings.WEBHOOK_RETRY_MAX_SECONDS = 10
    assert [delivery.retry_delay(number) for number in range(1, 6)] == [2, 4, 8, 10, 10]
