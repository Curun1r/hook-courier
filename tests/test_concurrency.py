from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from threading import Event as ThreadEvent

import httpx
import pytest
from django.db import close_old_connections, connection, connections
from rest_framework.test import APIClient

from deliveries import delivery
from deliveries.models import DeliveryAttempt, Destination, Event

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="Concurrent row locking and uniqueness require PostgreSQL.",
    ),
]

WAIT_SECONDS = 10


@contextmanager
def thread_connection():
    close_old_connections()
    database = connections["default"]
    try:
        with database.cursor() as cursor:
            cursor.execute("SET lock_timeout = '10s'")
            cursor.execute("SET statement_timeout = '15s'")
        yield database
    finally:
        database.close()


@pytest.mark.parametrize("conflicting_payload", [False, True])
def test_concurrent_idempotency_requests_create_one_event(
    settings, broker_publish, conflicting_payload
):
    settings.HOOKCOURIER_API_KEY = "concurrency-test-key"
    destination = Destination.objects.create(
        name="Concurrent receiver", url="https://example.com/webhook"
    )
    initial_reads = Barrier(2, timeout=WAIT_SECONDS)

    def submit(appointment_id):
        initial_lookup_seen = False

        def synchronize_initial_lookup(execute, sql, params, many, context):
            nonlocal initial_lookup_seen
            result = execute(sql, params, many, context)
            if (
                not initial_lookup_seen
                and sql.lstrip().startswith("SELECT")
                and 'FROM "deliveries_event"' in sql
                and '"idempotency_key"' in sql
            ):
                initial_lookup_seen = True
                # Both real SELECTs observe an absent key before either insert.
                initial_reads.wait()
            return result

        with thread_connection() as database:
            with database.execute_wrapper(synchronize_initial_lookup):
                response = APIClient().post(
                    "/api/v1/events/",
                    {
                        "destination_id": destination.pk,
                        "event_type": "appointment.created",
                        "payload": {"appointment_id": appointment_id},
                    },
                    format="json",
                    HTTP_X_API_KEY="concurrency-test-key",
                    HTTP_IDEMPOTENCY_KEY="concurrent-request",
                )
            return (
                response.status_code,
                response.data,
                response.get("Idempotency-Replayed"),
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(submit, 123),
            executor.submit(submit, 456 if conflicting_payload else 123),
        ]
        responses = [future.result(timeout=WAIT_SECONDS * 2) for future in futures]

    event = Event.objects.get()
    assert event.idempotency_key == "concurrent-request"
    assert event.status == Event.Status.PENDING
    assert DeliveryAttempt.objects.count() == 0
    broker_publish.assert_called_once_with(args=[str(event.pk)])
    if conflicting_payload:
        assert sorted(response[0] for response in responses) == [202, 409]
        accepted = next(response for response in responses if response[0] == 202)
        assert accepted[1]["id"] == str(event.pk)
        assert accepted[2] == "false"
    else:
        assert [response[0] for response in responses] == [202, 202]
        assert {response[1]["id"] for response in responses} == {str(event.pk)}
        assert sorted(response[2] for response in responses) == ["false", "true"]


def test_concurrent_worker_claims_make_one_http_request(monkeypatch):
    destination = Destination.objects.create(
        name="Concurrent receiver", url="https://example.com/webhook"
    )
    event = Event.objects.create(
        destination=destination,
        event_type="appointment.created",
        payload={"appointment_id": 123},
        request_hash="0" * 64,
    )
    first_has_lock = ThreadEvent()
    duplicate_claim_started = ThreadEvent()
    http_started = ThreadEvent()
    finish_http = ThreadEvent()
    requests = []
    original_client = httpx.AsyncClient

    def receive(request):
        requests.append(request)
        http_started.set()
        assert finish_http.wait(WAIT_SECONDS), "HTTP completion was not released."
        return httpx.Response(200)

    def mock_client(**kwargs):
        return original_client(transport=httpx.MockTransport(receive), **kwargs)

    monkeypatch.setattr(delivery.httpx, "AsyncClient", mock_client)

    def deliver(first):
        first_claim_seen = False

        def observe_claim(execute, sql, params, many, context):
            nonlocal first_claim_seen
            claim = (
                not first_claim_seen
                and 'FROM "deliveries_event"' in sql
                and "FOR UPDATE" in sql
            )
            if claim:
                first_claim_seen = True
                if not first:
                    duplicate_claim_started.set()
            result = execute(sql, params, many, context)
            if claim and first:
                # Keep PostgreSQL's row lock until the duplicate reaches its claim.
                first_has_lock.set()
                assert duplicate_claim_started.wait(WAIT_SECONDS)
            return result

        with thread_connection() as database:
            with database.execute_wrapper(observe_claim):
                return delivery.perform_delivery(str(event.pk))

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(deliver, True)
        try:
            assert first_has_lock.wait(WAIT_SECONDS), "First claim did not lock."
            duplicate = executor.submit(deliver, False)
            assert http_started.wait(WAIT_SECONDS), "HTTP request did not start."
            assert duplicate.result(timeout=WAIT_SECONDS).retry_after is None
            event.refresh_from_db()
            assert event.status == Event.Status.DELIVERING
            assert len(requests) == 1
        finally:
            finish_http.set()
        assert first.result(timeout=WAIT_SECONDS).retry_after is None

    event.refresh_from_db()
    assert event.status == Event.Status.DELIVERED
    attempt = event.attempts.get()
    assert (attempt.number, attempt.status_code) == (1, 200)
    assert len(requests) == 1


def test_concurrent_expired_lease_recovery_records_one_interruption():
    from datetime import timedelta

    from django.utils import timezone

    from deliveries.delivery import recover_expired_delivery

    destination = Destination.objects.create(name="Receiver", url="https://example.com")
    event = Event.objects.create(
        destination=destination,
        event_type="review.event",
        payload={},
        request_hash="0" * 64,
        status=Event.Status.DELIVERING,
        delivery_expires_at=timezone.now() - timedelta(seconds=1),
        delivery_started_at=timezone.now() - timedelta(minutes=2),
    )
    DeliveryAttempt.objects.create(event=event, number=1, duration_ms=0)
    barrier = Barrier(2, timeout=WAIT_SECONDS)

    def recover():
        with thread_connection():
            barrier.wait()
            return recover_expired_delivery(str(event.pk))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: recover(), range(2)))
    assert results == [True, True]
    event.refresh_from_db()
    assert event.status == Event.Status.PENDING and event.delivery_token is None
    assert event.attempts.count() == 1
    assert event.attempts.get().finished_at is not None
