import argparse
import csv
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import NotSupportedError

from deliveries.reporting import daily_delivery_report, explain_daily_report


def iso_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Use an ISO date in YYYY-MM-DD format"
        ) from exc


class Command(BaseCommand):
    help = "Count delivered and failed events by destination and UTC completion day."

    def add_arguments(self, parser):
        parser.add_argument("--from-date", required=True, type=iso_date)
        parser.add_argument("--to-date", required=True, type=iso_date)
        parser.add_argument("--destination-id", type=int)
        parser.add_argument(
            "--explain",
            action="store_true",
            help="Execute EXPLAIN (ANALYZE, BUFFERS) instead of printing CSV.",
        )

    def handle(self, *args, **options):
        report = explain_daily_report if options["explain"] else daily_delivery_report
        try:
            result = report(
                options["from_date"],
                options["to_date"],
                options["destination_id"],
            )
        except (ValueError, NotSupportedError) as exc:
            raise CommandError(str(exc)) from exc

        if options["explain"]:
            self.stdout.write(result)
            return

        writer = csv.writer(self.stdout)
        writer.writerow(["destination_id", "day", "delivered", "failed"])
        for row in result:
            writer.writerow([row.destination_id, row.day, row.delivered, row.failed])
