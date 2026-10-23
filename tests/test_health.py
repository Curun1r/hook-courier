from unittest.mock import patch

import pytest
from django.db import OperationalError
from django.urls import reverse


@pytest.mark.django_db
def test_health_reports_ready_without_disclosing_database_details(client):
    response = client.get(reverse("healthcheck"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "no-store" in response.headers["Cache-Control"]


def test_health_reports_unavailable_without_disclosing_error(client):
    with patch(
        "config.health.connection.cursor",
        side_effect=OperationalError("postgresql://private:password@db/private"),
    ):
        response = client.get(reverse("healthcheck"))

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "password" not in response.content.decode()


def test_health_does_not_accept_writes(client):
    response = client.post(reverse("healthcheck"))

    assert response.status_code == 405
