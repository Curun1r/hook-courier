import logging

import httpx
import pytest
from kombu.exceptions import OperationalError

from deliveries import delivery
from deliveries.models import Destination, Event
from deliveries.queueing import enqueue_event


@pytest.mark.parametrize("status_code", [200, 503])
def test_delivery_does_not_log_destination_secrets_at_info(
    monkeypatch, caplog, status_code
):
    token = "private-query-token"
    password = "private-url-password"
    destination = Destination(
        name="Private receiver",
        url=f"https://owner:{password}@example.com/webhook?token={token}",
    )
    event = Event(
        destination=destination,
        event_type="appointment.created",
        payload={"appointment_id": 123},
    )
    original_client = httpx.Client
    requests = []

    def receive(request):
        requests.append(request)
        return httpx.Response(status_code)

    def client(**kwargs):
        return original_client(transport=httpx.MockTransport(receive), **kwargs)

    monkeypatch.setattr(delivery.httpx, "Client", client)
    with caplog.at_level(logging.INFO):
        result = delivery.send_webhook(event)

    assert result.status_code == status_code
    assert len(requests) == 1
    assert requests[0].url.params["token"] == token
    assert token not in caplog.text
    assert password not in caplog.text
    assert "example.com/webhook" not in caplog.text


def test_application_warning_remains_visible_without_broker_secrets(
    caplog, broker_publish
):
    secret = "redis://owner:private-broker-password@example.com/0"
    broker_publish.side_effect = OperationalError(secret)
    event_id = "35ddab14-7616-4a84-8d0e-cd515553195a"

    with caplog.at_level(logging.INFO):
        queued = enqueue_event(event_id)

    assert queued is False
    assert f"Delivery could not be queued; event_id={event_id}" in caplog.text
    assert secret not in caplog.text
    assert "private-broker-password" not in caplog.text
