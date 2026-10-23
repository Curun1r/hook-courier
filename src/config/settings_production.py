import os

from django.core.exceptions import ImproperlyConfigured

from .settings import *  # noqa: F403
from .settings import (
    ALLOWED_HOSTS,
    DATABASES,
    HOOKCOURIER_API_KEY,
    MIDDLEWARE,
    SECRET_KEY,
)

DEBUG = False

if len(SECRET_KEY) < 50 or SECRET_KEY.startswith("change-this-"):
    raise ImproperlyConfigured(
        "Production requires a random DJANGO_SECRET_KEY of at least 50 characters."
    )
if len(HOOKCOURIER_API_KEY) < 32 or HOOKCOURIER_API_KEY.startswith("change-this-"):
    raise ImproperlyConfigured(
        "Production requires a random HOOKCOURIER_API_KEY of at least 32 characters."
    )
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("Production requires explicit DJANGO_ALLOWED_HOSTS.")
if DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
    raise ImproperlyConfigured("Production requires PostgreSQL.")

MIDDLEWARE = [
    MIDDLEWARE[0],
    "whitenoise.middleware.WhiteNoiseMiddleware",
    *MIDDLEWARE[1:],
]
STATIC_URL = "/static/"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}


def boolean_setting(name, default):
    value = os.getenv(name, default)
    if value not in {"0", "1"}:
        raise ImproperlyConfigured(f"{name} must be 0 or 1.")
    return value == "1"


# Disable HTTPS only for the documented rehearsal bound to the loopback interface.
HTTPS_ENABLED = boolean_setting("DJANGO_HTTPS", "1")
SECURE_SSL_REDIRECT = HTTPS_ENABLED
SESSION_COOKIE_SECURE = HTTPS_ENABLED
CSRF_COOKIE_SECURE = HTTPS_ENABLED
SECURE_HSTS_SECONDS = 3600 if HTTPS_ENABLED else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

# Enable only when a trusted proxy replaces client-supplied forwarding headers.
if boolean_setting("DJANGO_TRUST_PROXY", "0"):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
