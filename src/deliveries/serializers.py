from rest_framework import serializers

from .models import DeliveryAttempt, Event


class EventInputSerializer(serializers.Serializer):
    destination_id = serializers.IntegerField(min_value=1)
    event_type = serializers.RegexField(r"^[a-z][a-z0-9_.-]*$", max_length=100)
    payload = serializers.JSONField()

    def validate_payload(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Expected a JSON object.")
        return value


class DeliveryAttemptSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryAttempt
        fields = ("number", "attempted_at", "status_code", "duration_ms", "error")


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
            "created_at",
            "updated_at",
            "attempts",
        )
