import pytest
from rest_framework.test import APIClient

from deliveries.models import DeliveryAttempt, Destination, Event


@pytest.fixture(autouse=True)
def configure_api_key(settings):
    settings.HOOKCOURIER_API_KEY = "test-api-key"


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def destination(db):
    return Destination.objects.create(
        name="Demo receiver", url="https://example.com/webhook"
    )


def event_data(destination):
    return {
        "destination_id": destination.id,
        "event_type": "appointment.created",
        "payload": {"appointment_id": 123, "patient": {"name": "Ada"}},
    }


@pytest.mark.django_db
def test_post_creates_pending_event_and_get_returns_it(client, destination):
    response = client.post(
        "/api/v1/events/",
        event_data(destination),
        format="json",
        HTTP_X_API_KEY="test-api-key",
    )

    assert response.status_code == 202
    assert response.data["status"] == "pending"
    assert response.data["destination_id"] == destination.id
    assert response.data["attempts"] == []
    assert response["Location"].endswith(f"/api/v1/events/{response.data['id']}/")
    assert Event.objects.count() == 1

    detail = client.get(
        f"/api/v1/events/{response.data['id']}/", HTTP_X_API_KEY="test-api-key"
    )
    assert detail.status_code == 200
    assert detail.data["id"] == response.data["id"]


@pytest.mark.django_db
def test_api_key_is_required_for_post_and_get(client, destination):
    denied = client.post("/api/v1/events/", event_data(destination), format="json")
    assert denied.status_code == 403
    assert Event.objects.count() == 0

    event = Event.objects.create(
        destination=destination,
        event_type="appointment.created",
        payload={},
        request_hash="0" * 64,
    )
    denied = client.get(f"/api/v1/events/{event.id}/", HTTP_X_API_KEY="wrong")
    assert denied.status_code == 403


@pytest.mark.django_db
def test_idempotent_replay_uses_same_event_for_equivalent_json(client, destination):
    data = event_data(destination)
    first = client.post(
        "/api/v1/events/",
        data,
        format="json",
        HTTP_X_API_KEY="test-api-key",
        HTTP_IDEMPOTENCY_KEY="appointment-123",
    )
    data["payload"] = {"patient": {"name": "Ada"}, "appointment_id": 123}
    second = client.post(
        "/api/v1/events/",
        data,
        format="json",
        HTTP_X_API_KEY="test-api-key",
        HTTP_IDEMPOTENCY_KEY="appointment-123",
    )

    assert first.status_code == second.status_code == 202
    assert first.data["id"] == second.data["id"]
    assert second["Idempotency-Replayed"] == "true"
    assert Event.objects.count() == 1


@pytest.mark.django_db
def test_reusing_key_with_different_data_returns_conflict(client, destination):
    data = event_data(destination)
    first = client.post(
        "/api/v1/events/",
        data,
        format="json",
        HTTP_X_API_KEY="test-api-key",
        HTTP_IDEMPOTENCY_KEY="same-key",
    )
    data["payload"]["appointment_id"] = 456
    conflict = client.post(
        "/api/v1/events/",
        data,
        format="json",
        HTTP_X_API_KEY="test-api-key",
        HTTP_IDEMPOTENCY_KEY="same-key",
    )

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert Event.objects.count() == 1


@pytest.mark.django_db
def test_replay_still_works_after_destination_is_disabled(client, destination):
    data = event_data(destination)
    first = client.post(
        "/api/v1/events/",
        data,
        format="json",
        HTTP_X_API_KEY="test-api-key",
        HTTP_IDEMPOTENCY_KEY="appointment-123",
    )
    destination.is_active = False
    destination.save(update_fields=["is_active"])
    replay = client.post(
        "/api/v1/events/",
        data,
        format="json",
        HTTP_X_API_KEY="test-api-key",
        HTTP_IDEMPOTENCY_KEY="appointment-123",
    )
    new_event = client.post(
        "/api/v1/events/", data, format="json", HTTP_X_API_KEY="test-api-key"
    )

    assert replay.status_code == 202
    assert replay.data["id"] == first.data["id"]
    assert new_event.status_code == 400
    assert Event.objects.count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "change",
    [
        {"event_type": "INVALID TYPE"},
        {"payload": [1, 2]},
        {"destination_id": 999999},
    ],
)
def test_invalid_event_is_rejected(client, destination, change):
    data = event_data(destination) | change
    response = client.post(
        "/api/v1/events/", data, format="json", HTTP_X_API_KEY="test-api-key"
    )
    assert response.status_code == 400
    assert Event.objects.count() == 0


@pytest.mark.django_db
def test_get_includes_recorded_attempts(client, destination):
    event = Event.objects.create(
        destination=destination,
        event_type="appointment.created",
        payload={},
        request_hash="0" * 64,
    )
    DeliveryAttempt.objects.create(
        event=event, number=1, status_code=500, duration_ms=42
    )

    response = client.get(f"/api/v1/events/{event.id}/", HTTP_X_API_KEY="test-api-key")
    assert response.status_code == 200
    assert response.data["attempts"][0]["number"] == 1
    assert response.data["attempts"][0]["status_code"] == 500
