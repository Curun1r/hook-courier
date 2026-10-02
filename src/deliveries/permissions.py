from secrets import compare_digest

from django.conf import settings
from rest_framework.permissions import BasePermission


class HasAPIKey(BasePermission):
    message = "A valid X-API-Key header is required."

    def has_permission(self, request, view) -> bool:
        expected = settings.HOOKCOURIER_API_KEY
        supplied = request.headers.get("X-API-Key", "")
        return bool(expected) and compare_digest(
            supplied.encode("utf-8"), expected.encode("utf-8")
        )
