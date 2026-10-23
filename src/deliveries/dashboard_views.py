import json

from django.contrib.auth.mixins import UserPassesTestMixin
from django.db.models import Count
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.generic import DetailView, ListView

from .models import Event


class OwnerRequiredMixin(UserPassesTestMixin):
    login_url = "admin:login"

    def test_func(self):
        user = self.request.user
        return user.is_authenticated and user.is_active and user.is_superuser


@method_decorator(never_cache, name="dispatch")
class EventDashboardListView(OwnerRequiredMixin, ListView):
    template_name = "deliveries/event_list.html"
    context_object_name = "events"
    paginate_by = 25

    def get_queryset(self):
        events = (
            Event.objects.select_related("destination")
            .defer("payload", "request_hash", "idempotency_key")
            .annotate(attempt_count=Count("attempts"))
            .order_by("-created_at", "-id")
        )
        selected = self.request.GET.get("status", "")
        if selected in Event.Status.values:
            events = events.filter(status=selected)
        return events

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected = self.request.GET.get("status", "")
        context["selected_status"] = selected if selected in Event.Status.values else ""
        context["status_choices"] = Event.Status.choices
        return context


@method_decorator(never_cache, name="dispatch")
class EventDashboardDetailView(OwnerRequiredMixin, DetailView):
    template_name = "deliveries/event_detail.html"
    context_object_name = "event"
    queryset = Event.objects.select_related("destination").prefetch_related("attempts")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["payload_json"] = json.dumps(
            self.object.payload, ensure_ascii=False, indent=2, sort_keys=True
        )
        return context
