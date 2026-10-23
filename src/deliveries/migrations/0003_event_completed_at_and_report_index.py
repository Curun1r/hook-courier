from django.db import migrations, models


def backfill_completed_at(apps, schema_editor):
    event = apps.get_model("deliveries", "Event")
    event.objects.using(schema_editor.connection.alias).filter(
        status__in=["delivered", "failed"]
    ).update(completed_at=models.F("updated_at"))


class Migration(migrations.Migration):
    dependencies = [("deliveries", "0002_event_next_attempt_at")]

    operations = [
        migrations.AddField(
            model_name="event",
            name="completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_completed_at, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="event",
            index=models.Index(
                fields=["completed_at", "destination"],
                include=["status"],
                condition=models.Q(status__in=["delivered", "failed"]),
                name="event_terminal_report_idx",
            ),
        ),
    ]
