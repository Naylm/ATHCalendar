from django.db import migrations, models


def rename_default_site_name(apps, schema_editor):
    SiteSettings = apps.get_model("planning", "SiteSettings")
    SiteSettings.objects.filter(pk=1, site_name="ATH Calendar").update(
        site_name="Calendrier organisateurs ATH"
    )


def restore_default_site_name(apps, schema_editor):
    SiteSettings = apps.get_model("planning", "SiteSettings")
    SiteSettings.objects.filter(pk=1, site_name="Calendrier organisateurs ATH").update(
        site_name="ATH Calendar"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("planning", "0002_seed"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sitesettings",
            name="site_name",
            field=models.CharField(default="Calendrier organisateurs ATH", max_length=120),
        ),
        migrations.RunPython(rename_default_site_name, restore_default_site_name),
    ]
