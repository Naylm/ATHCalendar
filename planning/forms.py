import re
import uuid

from django import forms
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError as DjangoValidationError

from .models import GameType

DATE_INPUT = "%Y-%m-%d"


class LoginForm(forms.Form):
    password = forms.CharField(
        label="Mot de passe du Bureau",
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )


class PeriodCreateForm(forms.Form):
    label = forms.CharField(label="Libellé", max_length=120)
    start_date = forms.DateField(label="Début", input_formats=[DATE_INPUT])
    end_date = forms.DateField(label="Fin", input_formats=[DATE_INPUT])
    creation_key = forms.UUIDField(required=False)
    selected_dates = forms.CharField(required=False, widget=forms.HiddenInput)
    confirm_overlap = forms.BooleanField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["creation_key"].initial = uuid.uuid4()

    def clean_selected_dates(self):
        raw = self.cleaned_data.get("selected_dates") or ""
        dates = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                from datetime import date

                dates.append(date.fromisoformat(part))
            except ValueError:
                raise DjangoValidationError("Date sélectionnée invalide.") from None
        return dates


class PeriodEditForm(forms.Form):
    label = forms.CharField(label="Libellé", max_length=120)
    start_date = forms.DateField(label="Début", input_formats=[DATE_INPUT])
    end_date = forms.DateField(label="Fin", input_formats=[DATE_INPUT])
    expected_version = forms.IntegerField(min_value=0)


class EventForm(forms.Form):
    date = forms.DateField(label="Date", input_formats=[DATE_INPUT])
    game_type = forms.ModelChoiceField(
        queryset=GameType.objects.all(), required=False, label="Type"
    )
    title = forms.CharField(label="Titre", max_length=120, required=False)
    public_note = forms.CharField(
        label="Note publique", max_length=1000, required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    min_organizers = forms.IntegerField(
        label="Minimum d'organisateurs", min_value=1, required=False
    )
    capacity = forms.IntegerField(label="Capacité", min_value=1, required=False)
    expected_version = forms.IntegerField(min_value=0, required=False)


class OrganizerNameForm(forms.Form):
    name = forms.CharField(label="Pseudo", min_length=2, max_length=40)


class MergeForm(forms.Form):
    source = forms.UUIDField(label="Source")
    target = forms.UUIDField(label="Cible")


COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class GameTypeForm(forms.Form):
    name = forms.CharField(label="Nom", max_length=120)
    code = forms.SlugField(label="Code", max_length=40)
    description = forms.CharField(
        label="Description", required=False, widget=forms.Textarea(attrs={"rows": 3})
    )
    color = forms.CharField(label="Couleur", max_length=7, initial="#6b7c4a")

    def clean_color(self):
        value = self.cleaned_data["color"]
        if not COLOR_RE.match(value):
            raise DjangoValidationError("Couleur au format #rrggbb attendue.")
        return value.lower()


class GameTypeEditForm(forms.Form):
    name = forms.CharField(label="Nom", max_length=120)
    description = forms.CharField(
        label="Description", required=False, widget=forms.Textarea(attrs={"rows": 3})
    )
    color = forms.CharField(label="Couleur", max_length=7)

    def clean_color(self):
        value = self.cleaned_data["color"]
        if not COLOR_RE.match(value):
            raise DjangoValidationError("Couleur au format #rrggbb attendue.")
        return value.lower()


class PasswordChangeBureauForm(forms.Form):
    current_password = forms.CharField(
        label="Mot de passe actuel", widget=forms.PasswordInput
    )
    new_password = forms.CharField(
        label="Nouveau mot de passe", widget=forms.PasswordInput
    )

    def clean_new_password(self):
        value = self.cleaned_data["new_password"]
        password_validation.validate_password(value)
        return value


class BureauRegistrationForm(forms.Form):
    organizer = forms.UUIDField(label="Pseudo")
    state = forms.ChoiceField(choices=[("active", "Inscrire"), ("withdrawn", "Retirer")])
    note = forms.CharField(label="Note", max_length=500, required=False)
