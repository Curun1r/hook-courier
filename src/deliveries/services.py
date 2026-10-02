import hashlib
import json
from functools import partial

from django.db import transaction
from rest_framework.exceptions import APIException, ValidationError

from .models import Destination, Event
from .queueing import enqueue_event


class IdempotencyConflict(APIException):
    status_code = 409
    default_detail = "This idempotency key was already used with different event data."
    default_code = "idempotency_conflict"


def request_hash(*, destination_id: int, event_type: str, payload: dict) -> str:
    canonical = json.dumps(
        {
            "destination_id": destination_id,
            "event_type": event_type,
            "payload": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _replay_or_conflict(event: Event, digest: str) -> tuple[Event, bool]:
    if event.request_hash != digest:
        raise IdempotencyConflict()
    return event, True


def accept_event(
    *, destination_id: int, event_type: str, payload: dict, idempotency_key: str | None
) -> tuple[Event, bool]:
    digest = request_hash(
        destination_id=destination_id, event_type=event_type, payload=payload
    )

    with transaction.atomic():
        if idempotency_key is not None:
            existing = Event.objects.filter(idempotency_key=idempotency_key).first()
            if existing is not None:
                return _replay_or_conflict(existing, digest)

        destination = Destination.objects.filter(
            pk=destination_id, is_active=True
        ).first()
        if destination is None:
            raise ValidationError({"destination_id": "Active destination not found."})

        values = {
            "destination": destination,
            "event_type": event_type,
            "payload": payload,
            "request_hash": digest,
        }
        if idempotency_key is None:
            event = Event.objects.create(**values)
        else:
            event, created = Event.objects.get_or_create(
                idempotency_key=idempotency_key, defaults=values
            )
            if not created:
                return _replay_or_conflict(event, digest)

        transaction.on_commit(partial(enqueue_event, str(event.pk)))
        return event, False
