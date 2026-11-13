from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("planning", "0004_event_is_hidden")]

    operations = [
        migrations.AddField(
            model_name="event",
            name="availability",
            field=models.CharField(
                choices=[
                    ("available", "Ouverte"),
                    ("unavailable", "Indisponible"),
                    ("full", "Complète"),
                ],
                default="available",
                max_length=12,
            ),
        ),
        migrations.AddConstraint(
            model_name="event",
            constraint=models.CheckConstraint(
                condition=models.Q(availability__in=["available", "unavailable", "full"]),
                name="event_availability_valid",
            ),
        ),
    ]
