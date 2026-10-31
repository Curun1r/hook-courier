import asyncio
import logging
import math
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta

import httpx
from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import DeliveryAttempt, Event
from .payloads import encode_webhook_body

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HTTPResult:
    status_code: int | None
    error: str = ""
    retryable: bool = False


@dataclass(frozen=True)
class DeliveryOutcome:
    retry_after: int | None = None


def retry_delay(attempt_number: int) -> int:
    return min(
        settings.WEBHOOK_RETRY_BASE_SECONDS * 2 ** (attempt_number - 1),
        settings.WEBHOOK_RETRY_MAX_SECONDS,
    )


async def _send_http(url: str, body: bytes, event_id: str) -> HTTPResult:
    try:
        async with asyncio.timeout(settings.WEBHOOK_DEADLINE_SECONDS):
            async with httpx.AsyncClient(
                timeout=settings.WEBHOOK_TIMEOUT_SECONDS,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                # Read headers only. Cancellation closes the stream before another attempt.
                async with client.stream(
                    "POST",
                    url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-HookCourier-Event-ID": event_id,
                    },
                ) as response:
                    status_code = response.status_code
    except TimeoutError:
        return HTTPResult(None, "Delivery deadline exceeded", retryable=True)
    except httpx.InvalidURL:
        return HTTPResult(None, "Invalid destination URL")
    except httpx.RequestError as exc:
        # Exception messages can include credentials; record the class only.
        return HTTPResult(None, type(exc).__name__, retryable=True)

    if 200 <= status_code < 300:
        return HTTPResult(status_code)
    return HTTPResult(
        status_code,
        f"HTTP {status_code}",
        retryable=status_code == 429 or 500 <= status_code < 600,
    )


def send_webhook(event: Event) -> HTTPResult:
    if not event.destination.is_active:
        return HTTPResult(None, "Destination is inactive")
    try:
        body = encode_webhook_body(
            event_id=str(event.pk), event_type=event.event_type, payload=event.payload
        )
    except (TypeError, ValueError, RecursionError):
        return HTTPResult(None, "Invalid webhook payload")
    if len(body) > settings.WEBHOOK_MAX_BYTES:
        return HTTPResult(None, "Webhook body exceeds the size limit")
    return asyncio.run(_send_http(event.destination.url, body, str(event.pk)))


def _clear_claim(event: Event) -> None:
    event.delivery_token = None
    event.delivery_started_at = None
    event.delivery_expires_at = None


def _save_state(event: Event) -> None:
    event.save(
        update_fields=[
            "status",
            "next_attempt_at",
            "completed_at",
            "updated_at",
            "delivery_token",
            "delivery_started_at",
            "delivery_expires_at",
        ]
    )


def perform_delivery(event_id: str) -> DeliveryOutcome:
    with transaction.atomic():
        event = (
            Event.objects.select_for_update(of=("self",))
            .select_related("destination")
            .filter(pk=event_id)
            .first()
        )
        if event is None or event.status != Event.Status.PENDING:
            return DeliveryOutcome()
        now = timezone.now()
        if event.next_attempt_at is not None and event.next_attempt_at > now:
            return DeliveryOutcome(
                math.ceil((event.next_attempt_at - now).total_seconds())
            )
        previous_number = event.attempts.aggregate(last=Max("number"))["last"] or 0
        if previous_number >= settings.WEBHOOK_MAX_ATTEMPTS:
            event.status = Event.Status.FAILED
            event.next_attempt_at = None
            event.completed_at = now
            _clear_claim(event)
            _save_state(event)
            return DeliveryOutcome()

        token = uuid.uuid4()
        event.status = Event.Status.DELIVERING
        event.next_attempt_at = None
        event.delivery_token = token
        event.delivery_started_at = now
        event.delivery_expires_at = now + timedelta(
            seconds=settings.WEBHOOK_DELIVERY_LEASE_SECONDS
        )
        _save_state(event)
        # Reserve the attempt durably before HTTP, so interrupted attempts consume the budget.
        attempt = DeliveryAttempt.objects.create(
            event=event,
            number=previous_number + 1,
            attempted_at=now,
            duration_ms=0,
        )

    started = time.monotonic()
    try:
        result = send_webhook(event)
    except Exception as exc:
        # Soft limits and unexpected failures still leave a recorded retryable result.
        logger.warning(
            "Delivery interrupted; event_id=%s error_type=%s",
            event_id,
            type(exc).__name__,
        )
        result = HTTPResult(None, type(exc).__name__, retryable=True)
    duration_ms = max(0, round((time.monotonic() - started) * 1000))
    retry_after = None

    with transaction.atomic():
        event = Event.objects.select_for_update().get(pk=event_id)
        if event.status != Event.Status.DELIVERING or event.delivery_token != token:
            # A recovered/reclaimed attempt must never be overwritten by a late worker.
            return DeliveryOutcome()
        DeliveryAttempt.objects.filter(pk=attempt.pk).update(
            status_code=result.status_code,
            duration_ms=duration_ms,
            error=result.error,
            finished_at=timezone.now(),
        )
        if result.status_code is not None and 200 <= result.status_code < 300:
            event.status = Event.Status.DELIVERED
            event.completed_at = timezone.now()
        elif result.retryable and attempt.number < settings.WEBHOOK_MAX_ATTEMPTS:
            retry_after = retry_delay(attempt.number)
            event.status = Event.Status.PENDING
            event.next_attempt_at = timezone.now() + timedelta(seconds=retry_after)
        else:
            event.status = Event.Status.FAILED
            event.completed_at = timezone.now()
        _clear_claim(event)
        _save_state(event)
    return DeliveryOutcome(retry_after)


def recover_expired_delivery(event_id: str) -> bool:
    """Retire an expired claim; return whether this event can be queued again.

    Receivers must deduplicate event IDs: a missing result does not mean no side effect.
    """
    with transaction.atomic():
        event = Event.objects.select_for_update().filter(pk=event_id).first()
        if event is None:
            return False
        now = timezone.now()
        if event.status == Event.Status.PENDING:
            return event.next_attempt_at is None or event.next_attempt_at <= now
        if (
            event.status != Event.Status.DELIVERING
            or event.delivery_expires_at is None
            or event.delivery_expires_at > now
        ):
            return False
        attempt = (
            event.attempts.filter(finished_at__isnull=True).order_by("-number").first()
        )
        previous_number = event.attempts.aggregate(last=Max("number"))["last"] or 0
        if attempt is None and previous_number < settings.WEBHOOK_MAX_ATTEMPTS:
            # Older deliveries predate durable attempt reservation.
            attempt = DeliveryAttempt.objects.create(
                event=event,
                number=previous_number + 1,
                attempted_at=event.delivery_started_at or event.updated_at,
                duration_ms=0,
            )
        if attempt is not None:
            attempt.finished_at = now
            attempt.error = "Delivery interrupted; receiver outcome unknown"
            attempt.duration_ms = min(
                2_147_483_647,
                max(0, round((now - attempt.attempted_at).total_seconds() * 1000)),
            )
            attempt.save(update_fields=["finished_at", "error", "duration_ms"])
            previous_number = max(previous_number, attempt.number)
        event.status = (
            Event.Status.PENDING
            if previous_number < settings.WEBHOOK_MAX_ATTEMPTS
            else Event.Status.FAILED
        )
        event.next_attempt_at = now if event.status == Event.Status.PENDING else None
        event.completed_at = now if event.status == Event.Status.FAILED else None
        _clear_claim(event)
        _save_state(event)
        return event.status == Event.Status.PENDING
