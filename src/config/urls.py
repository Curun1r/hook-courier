from django.contrib import admin
from django.urls import include, path

from .health import healthcheck

urlpatterns = [
    path("healthz/", healthcheck, name="healthcheck"),
    path("admin/", admin.site.urls),
    path("api/v1/", include("deliveries.urls")),
    path("", include("deliveries.dashboard_urls")),
]
