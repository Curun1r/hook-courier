from django.conf import settings
from rest_framework import serializers

from .models import DeliveryAttempt, Event
from .payloads import encode_webhook_body


class EventInputSerializer(serializers.Serializer):
    destination_id = serializers.IntegerField(min_value=1)
    event_type = serializers.RegexField(r"^[a-z][a-z0-9_.-]*$", max_length=100)
    payload = serializers.JSONField()

    def validate_payload(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Expected a JSON object.")
        return value

    def validate(self, attrs):
        try:
            body = encode_webhook_body(
                event_id="00000000-0000-0000-0000-000000000000",
                event_type=attrs["event_type"],
                payload=attrs["payload"],
            )
        except (TypeError, ValueError, RecursionError):
            raise serializers.ValidationError(
                {"payload": "Expected valid UTF-8 JSON without null characters."}
            ) from None
        if len(body) > settings.WEBHOOK_MAX_BYTES:
            raise serializers.ValidationError(
                {
                    "payload": f"Webhook body must not exceed {settings.WEBHOOK_MAX_BYTES} bytes."
                }
            )
        return attrs


class DeliveryAttemptSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryAttempt
        fields = (
            "number",
            "attempted_at",
            "status_code",
            "duration_ms",
            "error",
            "finished_at",
        )


class EventSerializer(serializers.ModelSerializer):
    destination_id = serializers.IntegerField(read_only=True)
    attempts = DeliveryAttemptSerializer(many=True, read_only=True)

    class Meta:
        model = Event
        fields = (
            "id",
            "destination_id",
            "event_type",
            "payload",
            "status",
            "next_attempt_at",
            "delivery_expires_at",
            "created_at",
            "updated_at",
            "attempts",
        )
