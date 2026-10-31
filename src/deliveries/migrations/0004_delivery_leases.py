from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone


def backfill(apps, schema_editor):
    alias = schema_editor.connection.alias
    event = apps.get_model("deliveries", "Event")
    attempt = apps.get_model("deliveries", "DeliveryAttempt")
    batch = []
    for row in attempt.objects.using(alias).all().iterator(chunk_size=500):
        row.finished_at = row.attempted_at + timedelta(milliseconds=row.duration_ms)
        batch.append(row)
        if len(batch) == 500:
            attempt.objects.using(alias).bulk_update(
                batch, ["finished_at"], batch_size=500
            )
            batch = []
    if batch:
        attempt.objects.using(alias).bulk_update(batch, ["finished_at"], batch_size=500)
    # Drain old workers before migrating; legacy in-flight events get a recovery grace period.
    event.objects.using(alias).filter(status="delivering").update(
        delivery_started_at=models.F("updated_at"),
        delivery_expires_at=timezone.now() + timedelta(seconds=120),
    )


class Migration(migrations.Migration):
    dependencies = [("deliveries", "0003_event_completed_at_and_report_index")]
    operations = [
        migrations.AddField(
            model_name="event",
            name="delivery_token",
            field=models.UUIDField(null=True, blank=True, editable=False),
        ),
        migrations.AddField(
            model_name="event",
            name="delivery_started_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="event",
            name="delivery_expires_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="deliveryattempt",
            name="finished_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
