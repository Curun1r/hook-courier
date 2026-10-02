import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent
DEBUG = os.getenv("DJANGO_DEBUG", "0") == "1"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY and DEBUG:
    SECRET_KEY = "development-only-not-for-deployment"
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set")

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]
HOOKCOURIER_API_KEY = os.getenv("HOOKCOURIER_API_KEY", "")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "deliveries",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"

if os.getenv("DB_ENGINE") == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": PROJECT_ROOT / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB", "hookcourier"),
            "USER": os.getenv("POSTGRES_USER", "hookcourier"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", ""),
            "HOST": os.getenv("DB_HOST", "localhost"),
            "PORT": os.getenv("DB_PORT", "5432"),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
}

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_PUBLISH_RETRY = False
CELERY_BROKER_CONNECTION_TIMEOUT = 1
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "socket_connect_timeout": 1,
    "socket_timeout": 1,
}
CELERY_WORKER_PREFETCH_MULTIPLIER = 1


def positive_setting(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} must be a positive integer") from exc
    if value < 1:
        raise ImproperlyConfigured(f"{name} must be a positive integer")
    return value


WEBHOOK_MAX_ATTEMPTS = positive_setting("WEBHOOK_MAX_ATTEMPTS", 5)
WEBHOOK_TIMEOUT_SECONDS = positive_setting("WEBHOOK_TIMEOUT_SECONDS", 5)
WEBHOOK_MAX_BYTES = positive_setting("WEBHOOK_MAX_BYTES", 65536)
WEBHOOK_RETRY_BASE_SECONDS = positive_setting("WEBHOOK_RETRY_BASE_SECONDS", 2)
WEBHOOK_RETRY_MAX_SECONDS = positive_setting("WEBHOOK_RETRY_MAX_SECONDS", 60)
