from django.urls import path

from .dashboard_views import EventDashboardDetailView, EventDashboardListView

app_name = "dashboard"

urlpatterns = [
    path("", EventDashboardListView.as_view(), name="event-list"),
    path("events/<uuid:pk>/", EventDashboardDetailView.as_view(), name="event-detail"),
]
