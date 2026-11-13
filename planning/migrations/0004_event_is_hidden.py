from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("planning", "0003_rename_site_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="is_hidden",
            field=models.BooleanField(default=False),
        ),
    ]
