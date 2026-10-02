import pytest


@pytest.fixture(autouse=True)
def delivery_defaults(settings):
    settings.WEBHOOK_MAX_ATTEMPTS = 5
    settings.WEBHOOK_TIMEOUT_SECONDS = 5
    settings.WEBHOOK_MAX_BYTES = 65536
    settings.WEBHOOK_RETRY_BASE_SECONDS = 2
    settings.WEBHOOK_RETRY_MAX_SECONDS = 60
