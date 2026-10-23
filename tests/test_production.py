import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def production_process(code, **overrides):
    environment = (
        os.environ
        | {
            "PYTHONPATH": str(ROOT / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "DJANGO_SETTINGS_MODULE": "config.settings_production",
            "DJANGO_SECRET_KEY": "production-test-secret-" + "x" * 50,
            "HOOKCOURIER_API_KEY": "production-test-key-" + "x" * 32,
            "DJANGO_ALLOWED_HOSTS": "testserver",
            "DB_ENGINE": "postgresql",
            "DJANGO_HTTPS": "1",
            "DJANGO_TRUST_PROXY": "0",
        }
        | overrides
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"DJANGO_SECRET_KEY": "short"}, "DJANGO_SECRET_KEY"),
        ({"HOOKCOURIER_API_KEY": "short"}, "HOOKCOURIER_API_KEY"),
        ({"DJANGO_ALLOWED_HOSTS": "*"}, "DJANGO_ALLOWED_HOSTS"),
        ({"DB_ENGINE": "sqlite"}, "PostgreSQL"),
        ({"DJANGO_HTTPS": "invalid"}, "DJANGO_HTTPS"),
        ({"DJANGO_TRUST_PROXY": "invalid"}, "DJANGO_TRUST_PROXY"),
    ],
)
def test_production_rejects_invalid_configuration(overrides, message):
    result = production_process("import django; django.setup()", **overrides)

    assert result.returncode != 0
    assert message in result.stderr


@pytest.mark.parametrize(
    ("overrides", "expected_status"),
    [
        ({}, 301),
        ({"DJANGO_HTTPS": "0"}, 403),
        ({"DJANGO_TRUST_PROXY": "1"}, 403),
    ],
)
def test_https_and_proxy_boundary(overrides, expected_status):
    result = production_process(
        """
import json
import django
django.setup()
from django.conf import settings
from django.test import Client
response = Client().post('/api/v1/events/', data='{}', content_type='application/json', HTTP_X_FORWARDED_PROTO='https')
print(json.dumps({'status': response.status_code, 'location': response.get('Location'), 'debug': settings.DEBUG}))
""",
        **overrides,
    )

    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response["debug"] is False
    assert response["status"] == expected_status
    if expected_status == 301:
        assert response["location"].startswith("https://")
