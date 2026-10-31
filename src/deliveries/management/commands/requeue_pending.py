from argparse import ArgumentTypeError

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from deliveries.delivery import recover_expired_delivery
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
    help = "Queue pending events and optionally recover expired delivery leases."

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

        parser.add_argument(
            "--recover-expired",
            action="store_true",
            help="Recover expired delivery leases; receivers must deduplicate event IDs.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        if not isinstance(limit, int) or limit < 1:
            raise CommandError("Limit must be a positive integer.")

        now = timezone.now()
        eligible = Q(status=Event.Status.PENDING) & (
            Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now)
        )
        if options["recover_expired"]:
            eligible |= Q(status=Event.Status.DELIVERING, delivery_expires_at__lte=now)
        event_ids = list(
            Event.objects.filter(eligible)
            .order_by("created_at", "id")
            .values_list("id", flat=True)[:limit]
        )
        if options["dry_run"]:
            if options["recover_expired"]:
                self.stdout.write(
                    f"Dry run: {len(event_ids)} eligible event(s) would be inspected "
                    f"for pending/expired recovery (limit={limit}); no states were changed."
                )
                return
            self.stdout.write(
                f"Dry run: {len(event_ids)} due pending event(s) would be queued "
                f"(limit={limit})."
            )
            return

        queued = 0
        for event_id in event_ids:
            if options["recover_expired"] and not recover_expired_delivery(
                str(event_id)
            ):
                continue
            if not enqueue_event(str(event_id)):
                detail = (
                    "Recovered events remain pending; their attempt history is retained. "
                    if options["recover_expired"]
                    else "The command did not change delivery states. "
                )
                raise CommandError(
                    f"Broker publication failed after queuing {queued} event(s). "
                    + detail
                    + "Restore Redis and run requeue_pending again."
                )
            queued += 1

        self.stdout.write(
            self.style.SUCCESS(f"Queued {queued} due pending event(s) (limit={limit}).")
        )
