from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from deliveries.models import DeliveryAttempt, Destination, Event

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_user(
        username="owner", is_staff=True, is_superuser=True
    )


@pytest.fixture
def owner_client(client, owner):
    client.force_login(owner)
    return client


@pytest.fixture
def event():
    destination = Destination.objects.create(
        name="Demo receiver", url="https://example.com/webhook?token=private-token"
    )
    return Event.objects.create(
        destination=destination,
        event_type="appointment.created",
        payload={"appointment_id": 123},
        request_hash="0" * 64,
    )


def dashboard_url(page, event):
    return reverse(
        f"dashboard:event-{page}", args=[event.pk] if page == "detail" else []
    )


@pytest.mark.parametrize("page", ["list", "detail"])
def test_anonymous_user_is_sent_to_admin_login(client, event, page):
    url = dashboard_url(page, event)

    response = client.get(url)

    assert response.status_code == 302
    redirect = urlsplit(response.url)
    assert redirect.path == reverse("admin:login")
    assert parse_qs(redirect.query)["next"] == [url]


@pytest.mark.parametrize("page", ["list", "detail"])
@pytest.mark.parametrize("is_staff", [False, True])
def test_authenticated_non_owner_is_forbidden(
    client, django_user_model, event, page, is_staff
):
    user = django_user_model.objects.create_user(username="visitor", is_staff=is_staff)
    client.force_login(user)

    response = client.get(dashboard_url(page, event))

    assert response.status_code == 403
    assert str(event.pk) not in response.content.decode()


def test_inactive_owner_has_no_dashboard_access(client, owner):
    owner.is_active = False
    owner.save(update_fields=["is_active"])
    client.force_login(owner)

    response = client.get(reverse("dashboard:event-list"))

    assert response.status_code == 302
    assert urlsplit(response.url).path == reverse("admin:login")


def test_api_key_does_not_grant_dashboard_access(client, settings):
    settings.HOOKCOURIER_API_KEY = "test-api-key"

    response = client.get(
        reverse("dashboard:event-list"), HTTP_X_API_KEY="test-api-key"
    )

    assert response.status_code == 302


def test_owner_session_does_not_replace_api_key(owner_client, event):
    response = owner_client.get(reverse("event-detail", args=[event.pk]))

    assert response.status_code == 403


def test_owner_sees_event_list_and_attempt_count(owner_client, event):
    DeliveryAttempt.objects.create(
        event=event, number=1, status_code=503, duration_ms=42, error="HTTP 503"
    )

    response = owner_client.get(reverse("dashboard:event-list"))

    assert response.status_code == 200
    content = response.content.decode()
    assert str(event.pk) in content
    assert event.event_type in content
    assert event.destination.name in content
    assert reverse("dashboard:event-detail", args=[event.pk]) in content
    assert list(response.context["events"])[0].attempt_count == 1
    assert "private-token" not in content


def test_status_filter_only_shows_matching_events(owner_client, event):
    failed = Event.objects.create(
        destination=event.destination,
        event_type="appointment.cancelled",
        payload={},
        request_hash="0" * 64,
        status=Event.Status.FAILED,
    )

    response = owner_client.get(reverse("dashboard:event-list"), {"status": "failed"})

    assert response.status_code == 200
    assert [item.pk for item in response.context["events"]] == [failed.pk]
    assert response.context["selected_status"] == "failed"
    assert str(event.pk) not in response.content.decode()


def test_unknown_status_filter_is_ignored(owner_client, event):
    response = owner_client.get(reverse("dashboard:event-list"), {"status": "unknown"})

    assert response.status_code == 200
    assert [item.pk for item in response.context["events"]] == [event.pk]
    assert response.context["selected_status"] == ""


def test_list_is_paginated_and_preserves_status_filter(owner_client, event):
    Event.objects.bulk_create(
        [
            Event(
                destination=event.destination,
                event_type="appointment.created",
                payload={},
                request_hash="0" * 64,
            )
            for _ in range(25)
        ]
    )
    url = reverse("dashboard:event-list")

    first = owner_client.get(url, {"status": "pending"})
    second = owner_client.get(url, {"status": "pending", "page": 2})

    assert first.status_code == second.status_code == 200
    first_ids = {item.pk for item in first.context["events"]}
    second_ids = {item.pk for item in second.context["events"]}
    assert len(first_ids) == 25
    assert len(second_ids) == 1
    assert first_ids.isdisjoint(second_ids)
    assert first.context["paginator"].count == 26
    assert "status=pending&amp;page=2" in first.content.decode()


def test_detail_shows_payload_and_attempts_in_number_order(owner_client, event):
    event.status = Event.Status.DELIVERED
    event.save(update_fields=["status"])
    DeliveryAttempt.objects.create(
        event=event, number=2, status_code=200, duration_ms=18
    )
    DeliveryAttempt.objects.create(
        event=event, number=1, status_code=503, duration_ms=42, error="HTTP 503"
    )

    response = owner_client.get(reverse("dashboard:event-detail", args=[event.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Delivered" in content
    assert "appointment_id" in content and "123" in content
    assert "HTTP 503" in content and "42 ms" in content and "18 ms" in content
    assert content.index("#1") < content.index("#2")
    assert "private-token" not in content


def test_untrusted_payload_destination_and_error_are_escaped(owner_client, event):
    attack = '<img src=x onerror="alert(1)">'
    event.payload = {"message": attack}
    event.save(update_fields=["payload"])
    event.destination.name = attack
    event.destination.save(update_fields=["name"])
    DeliveryAttempt.objects.create(event=event, number=1, duration_ms=1, error=attack)

    detail = owner_client.get(reverse("dashboard:event-detail", args=[event.pk]))
    listing = owner_client.get(reverse("dashboard:event-list"))

    for response in (detail, listing):
        content = response.content.decode()
        assert response.status_code == 200
        assert attack not in content
        assert str(escape(attack)) in content
    assert "&lt;img" in detail.content.decode()


def test_empty_dashboard_has_clear_message(owner_client):
    response = owner_client.get(reverse("dashboard:event-list"))

    assert response.status_code == 200
    assert "No events found" in response.content.decode()


def test_missing_event_returns_404_for_owner(owner_client):
    response = owner_client.get(reverse("dashboard:event-detail", args=[uuid4()]))

    assert response.status_code == 404


@pytest.mark.parametrize("page", ["list", "detail"])
def test_owner_pages_are_not_cached(owner_client, event, page):
    response = owner_client.get(dashboard_url(page, event))

    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]
    assert "private" in response.headers["Cache-Control"]


def test_pending_detail_shows_next_attempt_and_empty_history(owner_client, event):
    event.next_attempt_at = timezone.now() + timedelta(minutes=1)
    event.save(update_fields=["next_attempt_at"])

    response = owner_client.get(reverse("dashboard:event-detail", args=[event.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Next attempt (UTC)" in content
    assert event.next_attempt_at.strftime("%Y-%m-%d %H:%M:%S") in content
    assert "No delivery attempts yet" in content
