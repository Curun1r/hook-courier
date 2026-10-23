from argparse import ArgumentTypeError

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from deliveries.models import Event
from deliveries.queueing import enqueue_event


def positive_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise ArgumentTypeError("Limit must be a positive integer.") from exc
    if limit < 1:
        raise ArgumentTypeError("Limit must be a positive integer.")
    return limit


class Command(BaseCommand):
    help = "Queue due pending events after a broker outage."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=positive_limit,
            default=100,
            help="Maximum number of events to queue (default: 100).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Count eligible events within the limit without queuing them.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        if not isinstance(limit, int) or limit < 1:
            raise CommandError("Limit must be a positive integer.")

        event_ids = list(
            Event.objects.filter(status=Event.Status.PENDING)
            .filter(
                Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=timezone.now())
            )
            .order_by("created_at", "id")
            .values_list("id", flat=True)[:limit]
        )
        if options["dry_run"]:
            self.stdout.write(
                f"Dry run: {len(event_ids)} due pending event(s) would be queued "
                f"(limit={limit})."
            )
            return

        queued = 0
        for event_id in event_ids:
            if not enqueue_event(str(event_id)):
                raise CommandError(
                    f"Broker publication failed after queuing {queued} event(s). "
                    "The command did not change delivery states. "
                    "Restore Redis and run requeue_pending again."
                )
            queued += 1

        self.stdout.write(
            self.style.SUCCESS(f"Queued {queued} due pending event(s) (limit={limit}).")
        )
