"""Collect immutable static assets during the image build without runtime secrets."""

from .settings import *  # noqa: F403

DEBUG = False
STATIC_URL = "/static/"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}
