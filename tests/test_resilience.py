import asyncio
import json
import threading
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from unittest.mock import patch

import httpx
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from kombu.exceptions import OperationalError
from rest_framework.test import APIClient

from deliveries.delivery import (
    HTTPResult,
    perform_delivery,
    recover_expired_delivery,
    send_webhook,
)
from deliveries.models import DeliveryAttempt, Destination, Event

pytestmark = pytest.mark.django_db


@pytest.fixture
def event():
    destination = Destination.objects.create(
        name="Receiver", url="https://example.com/webhook"
    )
    return Event.objects.create(
        destination=destination,
        event_type="review.created",
        payload={},
        request_hash="0" * 64,
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"text": "\ud800"},
        {"text": "\udfff"},
        {"text": "\x00"},
        {"\x00": "value"},
        {"items": [{"text": "\x00"}]},
        {"items": [{"\ud800": "value"}]},
    ],
)
def test_invalid_unicode_returns_400_without_a_saved_event(
    event, payload, settings, broker_publish
):
    settings.HOOKCOURIER_API_KEY = "test-key"
    count = Event.objects.count()
    response = APIClient().post(
        "/api/v1/events/",
        json.dumps(
            {
                "destination_id": event.destination_id,
                "event_type": "review.created",
                "payload": payload,
            }
        ),
        content_type="application/json",
        HTTP_X_API_KEY="test-key",
    )
    assert response.status_code == 400
    assert "payload" in response.data
    assert Event.objects.count() == count
    broker_publish.assert_not_called()


def test_valid_supplementary_unicode_is_accepted(event, settings):
    settings.HOOKCOURIER_API_KEY = "test-key"
    response = APIClient().post(
        "/api/v1/events/",
        json.dumps(
            {
                "destination_id": event.destination_id,
                "event_type": "review.created",
                "payload": {"text": "\U0001d11e\U0001f331"},
            }
        ),
        content_type="application/json",
        HTTP_X_API_KEY="test-key",
    )
    assert response.status_code == 202
    assert (
        Event.objects.get(pk=response.data["id"]).payload["text"]
        == "\U0001d11e\U0001f331"
    )


def test_worker_rejects_invalid_legacy_payload_without_http(event):
    event.payload = {"nested": [{"text": "\x00"}]}
    with patch("deliveries.delivery.httpx.AsyncClient") as client:
        result = send_webhook(event)
    assert result.error == "Invalid webhook payload"
    assert not result.retryable
    client.assert_not_called()


def test_claim_and_attempt_are_durable_before_http(event):
    def send(current):
        current.refresh_from_db()
        assert current.status == Event.Status.DELIVERING
        assert current.delivery_token is not None
        assert current.delivery_expires_at > timezone.now()
        attempt = current.attempts.get()
        assert attempt.number == 1 and attempt.finished_at is None
        return HTTPResult(200)

    with patch("deliveries.delivery.send_webhook", side_effect=send):
        perform_delivery(str(event.pk))
    event.refresh_from_db()
    assert event.attempts.get().finished_at is not None
    assert event.delivery_token is None
    assert event.delivery_started_at is None
    assert event.delivery_expires_at is None


def test_unexpected_exception_records_retry_without_secret_leaks(event, caplog):
    with patch(
        "deliveries.delivery.send_webhook",
        side_effect=RuntimeError("private-token-and-payload"),
    ):
        outcome = perform_delivery(str(event.pk))
    event.refresh_from_db()
    assert event.status == Event.Status.PENDING
    assert outcome.retry_after == 2
    assert event.attempts.get().error == "RuntimeError"
    assert event.attempts.get().finished_at is not None
    assert "private-token-and-payload" not in caplog.text


def interrupt(event):
    with patch(
        "deliveries.delivery.send_webhook",
        side_effect=SystemExit("simulated hard exit"),
    ):
        with pytest.raises(SystemExit):
            perform_delivery(str(event.pk))
    event.refresh_from_db()
    assert event.status == Event.Status.DELIVERING


def expire(event):
    Event.objects.filter(pk=event.pk).update(
        delivery_expires_at=timezone.now() - timedelta(seconds=1)
    )


def test_expired_delivery_recovery_keeps_history_and_event_identity(
    event, broker_publish
):
    interrupt(event)
    original_id = event.pk
    call_command("requeue_pending", recover_expired=True, stdout=StringIO())
    broker_publish.assert_not_called()
    expire(event)
    before = list(Event.objects.values())
    output = StringIO()
    call_command("requeue_pending", recover_expired=True, dry_run=True, stdout=output)
    assert "no states were changed" in output.getvalue()
    assert list(Event.objects.values()) == before
    assert event.attempts.get().finished_at is None
    call_command("requeue_pending", recover_expired=True, stdout=StringIO())
    broker_publish.assert_called_once_with(args=[str(original_id)])
    event.refresh_from_db()
    assert event.status == Event.Status.PENDING and event.delivery_token is None
    interrupted = event.attempts.get()
    assert interrupted.finished_at is not None
    assert interrupted.error == "Delivery interrupted; receiver outcome unknown"
    with patch("deliveries.delivery.send_webhook", return_value=HTTPResult(200)):
        perform_delivery(str(event.pk))
    event.refresh_from_db()
    assert event.pk == original_id and event.status == Event.Status.DELIVERED
    assert list(event.attempts.values_list("number", "status_code")) == [
        (1, None),
        (2, 200),
    ]


def test_repeated_interruptions_exhaust_the_attempt_budget(event, broker_publish):
    for number in range(1, 6):
        interrupt(event)
        expire(event)
        call_command("requeue_pending", recover_expired=True, stdout=StringIO())
        assert event.attempts.count() == number
    event.refresh_from_db()
    assert event.status == Event.Status.FAILED and event.completed_at is not None
    assert broker_publish.call_count == 4
    assert not event.attempts.filter(finished_at__isnull=True).exists()
    with patch("deliveries.delivery.send_webhook") as send:
        perform_delivery(str(event.pk))
    send.assert_not_called()


def test_late_worker_result_cannot_overwrite_new_attempt(event):
    def old_request(current):
        expire(current)
        assert recover_expired_delivery(str(current.pk))
        with patch(
            "deliveries.delivery.send_webhook",
            return_value=HTTPResult(503, "HTTP 503", True),
        ):
            perform_delivery(str(current.pk))
        return HTTPResult(200)

    with patch("deliveries.delivery.send_webhook", side_effect=old_request):
        outcome = perform_delivery(str(event.pk))
    event.refresh_from_db()
    assert outcome.retry_after is None
    assert event.status == Event.Status.PENDING
    assert (
        event.attempts.get(number=1).error
        == "Delivery interrupted; receiver outcome unknown"
    )
    assert event.attempts.get(number=2).status_code == 503


def test_recovery_broker_failure_keeps_the_recovered_event_pending(
    event, broker_publish
):
    interrupt(event)
    expire(event)
    broker_publish.side_effect = OperationalError("secret-url")
    with pytest.raises(CommandError, match="Recovered events remain pending"):
        call_command("requeue_pending", recover_expired=True, stdout=StringIO())
    event.refresh_from_db()
    assert event.status == Event.Status.PENDING
    assert event.attempts.get().finished_at is not None


def test_total_deadline_cancels_the_request_and_records_retry(
    event, settings, monkeypatch
):
    settings.WEBHOOK_DEADLINE_SECONDS = 0.08
    original = httpx.AsyncClient
    cancelled = []

    async def receive(request):
        try:
            await asyncio.sleep(2)
            return httpx.Response(200)
        finally:
            cancelled.append(True)

    monkeypatch.setattr(
        "deliveries.delivery.httpx.AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(receive), **kw),
    )
    started = time.monotonic()
    outcome = perform_delivery(str(event.pk))
    assert time.monotonic() - started < 1
    event.refresh_from_db()
    assert event.status == Event.Status.PENDING and outcome.retry_after == 2
    assert event.attempts.get().error == "Delivery deadline exceeded"
    assert event.attempts.get().finished_at is not None and cancelled == [True]


def test_real_trickling_headers_cannot_extend_total_deadline(event, settings):
    settings.WEBHOOK_TIMEOUT_SECONDS = 0.2
    settings.WEBHOOK_DEADLINE_SECONDS = 0.35
    stop = threading.Event()

    class SlowHeaders(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            try:
                self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                self.wfile.flush()
                for _ in range(100):
                    if stop.wait(0.025):
                        return
                    self.wfile.write(b"x")
                    self.wfile.flush()
                self.wfile.write(b"\r\nContent-Length: 0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHeaders)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    event.destination.url = f"http://127.0.0.1:{server.server_port}/webhook"
    try:
        started = time.monotonic()
        result = send_webhook(event)
        elapsed = time.monotonic() - started
        assert result.error == "Delivery deadline exceeded" and result.retryable
        assert 0.3 <= elapsed < 1.5
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.django_db(transaction=True)
def test_lease_migration_backfills_history_and_legacy_claims():
    old = ("deliveries", "0003_event_completed_at_and_report_index")
    latest = ("deliveries", "0004_delivery_leases")
    executor = MigrationExecutor(connection)
    executor.migrate([old])
    try:
        apps = executor.loader.project_state([old]).apps
        dest = apps.get_model("deliveries", "Destination").objects.create(
            name="Old", url="https://example.com"
        )
        row = apps.get_model("deliveries", "Event").objects.create(
            destination=dest,
            event_type="old.event",
            payload={},
            request_hash="0" * 64,
            status="delivering",
        )
        attempt = apps.get_model("deliveries", "DeliveryAttempt").objects.create(
            event=row, number=1, duration_ms=250
        )
        MigrationExecutor(connection).migrate([latest])
        saved = Event.objects.get(pk=row.pk)
        assert saved.delivery_expires_at > timezone.now()
        assert saved.delivery_started_at is not None
        history = DeliveryAttempt.objects.get(pk=attempt.pk)
        assert history.finished_at == history.attempted_at + timedelta(milliseconds=250)
        expire(saved)
        assert recover_expired_delivery(str(saved.pk))
        assert saved.attempts.count() == 2
    finally:
        MigrationExecutor(connection).migrate([latest])


def test_celery_fallback_limits_fit_inside_delivery_lease(settings):
    from deliveries.tasks import deliver_event

    assert deliver_event.soft_time_limit == settings.CELERY_TASK_SOFT_TIME_LIMIT
    assert deliver_event.time_limit == settings.CELERY_TASK_TIME_LIMIT
    assert settings.WEBHOOK_DEADLINE_SECONDS < deliver_event.soft_time_limit
    assert deliver_event.soft_time_limit < deliver_event.time_limit
    assert deliver_event.time_limit < settings.WEBHOOK_DELIVERY_LEASE_SECONDS
