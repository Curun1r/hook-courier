from django.contrib import admin

from .models import DeliveryAttempt, Destination, Event


@admin.register(Destination)
class DestinationAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "url", "is_active", "created_at")
    readonly_fields = ("id",)
    list_filter = ("is_active",)
    search_fields = ("name", "url")


class DeliveryAttemptInline(admin.TabularInline):
    model = DeliveryAttempt
    extra = 0
    can_delete = False
    readonly_fields = ("number", "attempted_at", "status_code", "duration_ms", "error")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("id", "event_type", "destination", "status", "created_at")
    list_filter = ("status", "destination")
    search_fields = ("id", "event_type", "idempotency_key")
    readonly_fields = (
        "id",
        "destination",
        "event_type",
        "payload",
        "idempotency_key",
        "request_hash",
        "status",
        "next_attempt_at",
        "created_at",
        "updated_at",
    )
    inlines = (DeliveryAttemptInline,)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
