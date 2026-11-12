import getpass

from django.contrib.auth.models import Group, User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Crée le compte Bureau ou fait tourner son mot de passe (interactif)."

    def handle(self, *args, **options):
        group, _ = Group.objects.get_or_create(name="Bureau")
        user = User.objects.filter(username="bureau").first()
        if user is None:
            self.stdout.write("Création du compte Bureau.")
            user = User(username="bureau")
        else:
            self.stdout.write(
                "Le compte Bureau existe ; le mot de passe va être remplacé."
            )
        password1 = getpass.getpass("Nouveau mot de passe du Bureau : ")
        password2 = getpass.getpass("Confirmation : ")
        if password1 != password2:
            raise CommandError("Les deux saisies ne correspondent pas.")
        try:
            validate_password(password1, user=user)
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc
        user.set_password(password1)
        user.is_staff = False
        user.is_superuser = False
        user.save()
        user.groups.add(group)
        self.stdout.write(self.style.SUCCESS("Compte Bureau mis à jour."))
