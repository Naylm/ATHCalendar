import json
import re
import uuid

from django import forms
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError as DjangoValidationError

from .models import GameType
from .selectors import BUREAU_MANAGEMENT_FILTERS, BUREAU_STAFFING_FILTERS
from .services.types import ALLOWED_COLORS

DATE_INPUT = "%Y-%m-%d"


def _date_widget():
    return forms.DateInput(attrs={"type": "date"}, format=DATE_INPUT)


COLOR_CHOICES = [(color, color) for color in ALLOWED_COLORS]


class LoginForm(forms.Form):
    password = forms.CharField(
        label="Mot de passe du Bureau",
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )


class PeriodCreateForm(forms.Form):
    label = forms.CharField(label="Libellé", max_length=120)
    start_date = forms.DateField(label="Début", input_formats=[DATE_INPUT], widget=_date_widget())
    end_date = forms.DateField(label="Fin", input_formats=[DATE_INPUT], widget=_date_widget())
    creation_key = forms.UUIDField(required=True, widget=forms.HiddenInput)
    selected_dates = forms.CharField(required=False, widget=forms.HiddenInput)
    previewed = forms.BooleanField(required=False, widget=forms.HiddenInput)
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

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")
        if start and end and start > end:
            raise DjangoValidationError("La date de début doit précéder ou égaler la date de fin.")
        return cleaned


class PeriodEditForm(forms.Form):
    label = forms.CharField(label="Libellé", max_length=120)
    start_date = forms.DateField(label="Début", input_formats=[DATE_INPUT], widget=_date_widget())
    end_date = forms.DateField(label="Fin", input_formats=[DATE_INPUT], widget=_date_widget())
    expected_version = forms.IntegerField(min_value=0)

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")
        if start and end and start > end:
            raise DjangoValidationError("La date de début doit précéder ou égaler la date de fin.")
        return cleaned


class EventForm(forms.Form):
    date = forms.DateField(label="Date", input_formats=[DATE_INPUT], widget=_date_widget())
    game_type = forms.ModelChoiceField(
        queryset=GameType.objects.all(), required=False, label="Type"
    )
    title = forms.CharField(label="Titre", max_length=120, required=False)
    public_note = forms.CharField(
        label="Note publique",
        max_length=1000,
        required=False,
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
    color = forms.ChoiceField(label="Couleur", choices=COLOR_CHOICES)

    def clean_color(self):
        value = self.cleaned_data["color"].lower()
        if value not in ALLOWED_COLORS:
            raise DjangoValidationError(
                "Couleur non autorisée ; choisissez dans la palette proposée."
            )
        return value


class GameTypeEditForm(forms.Form):
    name = forms.CharField(label="Nom", max_length=120)
    description = forms.CharField(
        label="Description", required=False, widget=forms.Textarea(attrs={"rows": 3})
    )
    color = forms.ChoiceField(label="Couleur", choices=COLOR_CHOICES)

    def clean_color(self):
        value = self.cleaned_data["color"].lower()
        if value not in ALLOWED_COLORS:
            raise DjangoValidationError(
                "Couleur non autorisée ; choisissez dans la palette proposée."
            )
        return value


class PasswordChangeBureauForm(forms.Form):
    current_password = forms.CharField(label="Mot de passe actuel", widget=forms.PasswordInput)
    new_password = forms.CharField(label="Nouveau mot de passe", widget=forms.PasswordInput)

    def clean_new_password(self):
        value = self.cleaned_data["new_password"]
        password_validation.validate_password(value)
        return value


class BureauRegistrationForm(forms.Form):
    organizer = forms.UUIDField(label="Pseudo")
    state = forms.ChoiceField(choices=[("active", "Inscrire"), ("withdrawn", "Retirer")])
    note = forms.CharField(label="Note", max_length=500, required=False)


class PeriodFinalizeForm(forms.Form):
    keep_event_ids = forms.MultipleChoiceField(required=False)
    expected_revision = forms.IntegerField(min_value=0)
    confirm_close = forms.BooleanField(required=True, label="Confirmer la clôture")

    def __init__(self, *args, candidates=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["keep_event_ids"].choices = [
            (str(event.pk), str(event.pk)) for event in candidates or []
        ]


class DateCleanupFilterForm(forms.Form):
    staffing = forms.ChoiceField(label="Inscriptions", choices=BUREAU_STAFFING_FILTERS)


class DateCleanupForm(forms.Form):
    event_ids = forms.MultipleChoiceField(required=True)
    expected_revision = forms.IntegerField(min_value=0)
    staffing = forms.ChoiceField(choices=BUREAU_STAFFING_FILTERS, widget=forms.HiddenInput)
    confirm_hide = forms.BooleanField(required=True, label="Confirmer le masquage")

    def __init__(self, *args, candidates=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["event_ids"].choices = [
            (str(event.pk), str(event.pk)) for event in candidates or []
        ]


class PeriodManagementFilterForm(forms.Form):
    staffing = forms.ChoiceField(label="Afficher", choices=BUREAU_MANAGEMENT_FILTERS)


class DateVisibilityForm(forms.Form):
    event_id = forms.UUIDField()
    visible = forms.BooleanField(required=False)
    expected_revision = forms.IntegerField(min_value=0)
    expected_version = forms.IntegerField(min_value=1)
    staffing = forms.ChoiceField(
        choices=BUREAU_MANAGEMENT_FILTERS, required=False, widget=forms.HiddenInput
    )


class PeriodSelectionForm(forms.Form):
    event_ids = forms.MultipleChoiceField(required=True)
    selection_action = forms.ChoiceField(
        required=True,
        choices=[
            ("", "Action…"),
            ("hide", "Masquer les dates"),
            ("unhide", "Réafficher les dates"),
        ],
    )
    staffing = forms.ChoiceField(choices=BUREAU_MANAGEMENT_FILTERS, widget=forms.HiddenInput)
    expected_revision = forms.IntegerField(min_value=0)
    confirm = forms.BooleanField(required=True)

    def __init__(self, *args, candidates=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["event_ids"].choices = [
            (str(event.pk), str(event.pk)) for event in candidates or []
        ]

    def clean(self):
        cleaned = super().clean()
        staffing = cleaned.get("staffing")
        action = cleaned.get("selection_action")
        if staffing is not None and action is not None:
            if staffing == "hidden" and action != "unhide":
                raise DjangoValidationError(
                    "Seule la réaffichage est possible sur les dates masquées."
                )
            if staffing != "hidden" and action != "hide":
                raise DjangoValidationError("Seul le masquage est possible sur ces dates.")
        return cleaned


class EventsBatchForm(forms.Form):
    event_ids = forms.MultipleChoiceField(required=True)
    game_type = forms.ChoiceField(required=False)
    min_organizers = forms.IntegerField(min_value=1, required=False)
    capacity_mode = forms.ChoiceField(
        required=False,
        choices=[
            ("unchanged", "Ne pas changer"),
            ("unlimited", "Illimitée"),
            ("value", "Valeur"),
        ],
    )
    capacity_value = forms.IntegerField(min_value=1, required=False)
    registrations = forms.ChoiceField(
        required=False, choices=[("", ""), ("open", "Ouvrir"), ("close", "Fermer")]
    )
    status = forms.ChoiceField(
        required=False,
        choices=[("", ""), ("cancel", "Annuler"), ("restore", "Restaurer")],
    )
    versions_json = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, period=None, types=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.period = period
        if period is not None:
            self.fields["event_ids"].choices = [
                (str(pk), str(pk)) for pk in period.events.values_list("pk", flat=True)
            ]
        type_choices = [("", "— ne pas changer —"), ("none", "À définir")]
        for game_type in types or []:
            type_choices.append((str(game_type.pk), game_type.name))
        self.fields["game_type"].choices = type_choices

    def clean_event_ids(self):
        return list(self.cleaned_data["event_ids"])

    def clean_game_type(self):
        from .services.events import UNSET

        value = self.cleaned_data["game_type"]
        if value == "":
            return UNSET
        if value == "none":
            return None
        return value

    def clean_versions_json(self):
        raw = self.cleaned_data.get("versions_json") or ""
        if not raw.strip():
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise DjangoValidationError("Versions invalides.") from None
        if not isinstance(data, dict):
            raise DjangoValidationError("Versions invalides.")
        parsed = {}
        for key, value in data.items():
            try:
                uuid.UUID(str(key))
            except (ValueError, AttributeError, TypeError):
                raise DjangoValidationError("Versions invalides.") from None
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise DjangoValidationError("Versions invalides.")
            parsed[str(key)] = value
        return parsed


class SiteSettingsForm(forms.Form):
    site_name = forms.CharField(label="Nom du site", min_length=1, max_length=120)
    default_min_organizers = forms.IntegerField(
        label="Minimum d'organisateurs par défaut", min_value=1
    )
    default_capacity = forms.IntegerField(
        label="Capacité par défaut (vide = illimitée)",
        required=False,
        min_value=1,
    )

    def clean(self):
        cleaned = super().clean()
        minimum = cleaned.get("default_min_organizers")
        capacity = cleaned.get("default_capacity")
        if minimum is not None and capacity is not None and capacity < minimum:
            raise DjangoValidationError(
                "La capacité par défaut doit être supérieure ou égale au minimum."
            )
        return cleaned
