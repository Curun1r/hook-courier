import math
import time
from dataclasses import dataclass
from datetime import timedelta

import httpx
from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import DeliveryAttempt, Event
from .payloads import encode_webhook_body


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


def send_webhook(event: Event) -> HTTPResult:
    if not event.destination.is_active:
        return HTTPResult(None, "Destination is inactive")

    body = encode_webhook_body(
        event_id=str(event.pk), event_type=event.event_type, payload=event.payload
    )
    if len(body) > settings.WEBHOOK_MAX_BYTES:
        return HTTPResult(None, "Webhook body exceeds the size limit")

    try:
        with httpx.Client(
            timeout=settings.WEBHOOK_TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            # Read response headers only; receiver bodies are never stored or logged.
            with client.stream(
                "POST",
                event.destination.url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-HookCourier-Event-ID": str(event.pk),
                },
            ) as response:
                status_code = response.status_code
    except httpx.InvalidURL:
        return HTTPResult(None, "Invalid destination URL")
    except httpx.RequestError as exc:
        # Exception messages can contain URLs and credentials; retain the class only.
        return HTTPResult(None, type(exc).__name__, retryable=True)

    if 200 <= status_code < 300:
        return HTTPResult(status_code)
    return HTTPResult(
        status_code,
        f"HTTP {status_code}",
        retryable=status_code == 429 or 500 <= status_code < 600,
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
            event.save(update_fields=["status", "next_attempt_at", "updated_at"])
            return DeliveryOutcome()

        event.status = Event.Status.DELIVERING
        event.next_attempt_at = None
        event.save(update_fields=["status", "next_attempt_at", "updated_at"])

    attempted_at = timezone.now()
    started = time.monotonic()
    result = send_webhook(event)
    duration_ms = max(0, round((time.monotonic() - started) * 1000))
    number = previous_number + 1
    retry_after = None

    with transaction.atomic():
        event = Event.objects.select_for_update().get(pk=event_id)
        DeliveryAttempt.objects.create(
            event=event,
            number=number,
            attempted_at=attempted_at,
            status_code=result.status_code,
            duration_ms=duration_ms,
            error=result.error,
        )
        if result.status_code is not None and 200 <= result.status_code < 300:
            event.status = Event.Status.DELIVERED
        elif result.retryable and number < settings.WEBHOOK_MAX_ATTEMPTS:
            retry_after = retry_delay(number)
            event.status = Event.Status.PENDING
            event.next_attempt_at = timezone.now() + timedelta(seconds=retry_after)
        else:
            event.status = Event.Status.FAILED
        event.save(update_fields=["status", "next_attempt_at", "updated_at"])

    return DeliveryOutcome(retry_after)
