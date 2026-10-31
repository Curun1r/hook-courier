import uuid

from django.db import models
from django.utils import timezone


class Destination(models.Model):
    name = models.CharField(max_length=120)
    url = models.URLField(max_length=2048)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name


class Event(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        DELIVERING = "delivering", "Delivering"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    destination = models.ForeignKey(
        Destination, on_delete=models.PROTECT, related_name="events"
    )
    event_type = models.CharField(max_length=100)
    payload = models.JSONField()
    idempotency_key = models.CharField(
        max_length=255, null=True, blank=True, unique=True
    )
    request_hash = models.CharField(max_length=64)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING
    )
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    delivery_token = models.UUIDField(null=True, blank=True, editable=False)
    delivery_started_at = models.DateTimeField(null=True, blank=True)
    delivery_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["status", "created_at"], name="event_status_created_idx"
            ),
            models.Index(
                fields=["completed_at", "destination"],
                include=["status"],
                condition=models.Q(status__in=["delivered", "failed"]),
                name="event_terminal_report_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} ({self.id})"


class DeliveryAttempt(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="attempts")
    number = models.PositiveSmallIntegerField()
    attempted_at = models.DateTimeField(default=timezone.now)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField()
    error = models.TextField(blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "number"], name="unique_attempt_number_per_event"
            )
        ]

    def __str__(self) -> str:
        return f"{self.event_id} attempt {self.number}"
