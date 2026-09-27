from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Event
from .permissions import HasAPIKey
from .serializers import EventInputSerializer, EventSerializer
from .services import accept_event


class EventListView(APIView):
    permission_classes = [HasAPIKey]

    def post(self, request):
        serializer = EventInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        key = request.headers.get("Idempotency-Key")
        if key is not None:
            key = key.strip()
            if not key or len(key) > 255:
                return Response(
                    {"idempotency_key": ["Must contain 1 to 255 characters."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        event, replayed = accept_event(**serializer.validated_data, idempotency_key=key)
        location = request.build_absolute_uri(reverse("event-detail", args=[event.pk]))
        return Response(
            EventSerializer(event).data,
            status=status.HTTP_202_ACCEPTED,
            headers={
                "Location": location,
                "Idempotency-Replayed": str(replayed).lower(),
            },
        )


class EventDetailView(APIView):
    permission_classes = [HasAPIKey]

    def get(self, request, event_id):
        event = get_object_or_404(
            Event.objects.select_related("destination").prefetch_related("attempts"),
            pk=event_id,
        )
        return Response(EventSerializer(event).data)
