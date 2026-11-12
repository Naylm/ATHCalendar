from django.db import migrations


def seed(apps, schema_editor):
    GameType = apps.get_model("planning", "GameType")
    GameType.objects.get_or_create(
        code="flex",
        defaults={
            "name": "Flex",
            "description": (
                "Parties ouvertes à tous, y compris débutants et joueurs externes, "
                "avec des scénarios relativement simples."
            ),
            "color": "#6b7c4a",
        },
    )
    GameType.objects.get_or_create(
        code="roltac",
        defaults={
            "name": "Roltac",
            "description": (
                "Parties plus scénarisées et élaborées pour les joueurs "
                "expérimentés ATH et les joueurs externes."
            ),
            "color": "#b08a3e",
        },
    )
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name="Bureau")


class Migration(migrations.Migration):
    dependencies = [
        ("planning", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
