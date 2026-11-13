import re
import unicodedata
from dataclasses import dataclass

from planning.db import write_transaction
from planning.errors import Conflict, NotFound, ValidationFailed
from planning.models import Event, GameType

from . import audit
from .actors import Actor
from .periods import _require_bureau, bump_revision
from .validators import optional_str, parse_uuid, require_bool, require_str

ALLOWED_COLORS = (
    "#6b7c4a",
    "#b08a3e",
    "#526777",
    "#60766b",
    "#8a5b55",
)
COLOR_PALETTE = tuple(
    {"value": color, "label": label, "css_class": f"type-c{index}"}
    for index, (color, label) in enumerate(
        zip(ALLOWED_COLORS, ("Olive", "Sable", "Ardoise", "Sauge", "Terre"), strict=True),
        start=1,
    )
)


def get_type(type_id) -> GameType:
    try:
        return GameType.objects.get(pk=parse_uuid(type_id, "Type"))
    except GameType.DoesNotExist:
        raise NotFound("Type de partie introuvable.") from None


def _validate_color(color):
    if not isinstance(color, str) or color.lower() not in ALLOWED_COLORS:
        raise ValidationFailed(
            "Couleur non autorisée ; choisissez dans la palette proposée.",
            fields={"color": str(color)[:20]},
        )
    return color.lower()


def _validate_name(name):
    return require_str(name, "Le nom du type", min_len=1, max_len=120, field="name")


def validate_code(code):
    code = unicodedata.normalize(
        "NFC", require_str(code, "Le code du type", min_len=1, max_len=40, field="code")
    )
    if re.fullmatch(r"[\w.-]+", code) is None:
        raise ValidationFailed(
            "Le code accepte les lettres accentuées, chiffres, points, tirets et tirets bas.",
            fields={"code": "Caractères non autorisés."},
        )
    return code


def create_type(*, actor: Actor, code, name, description=None, color):
    _require_bureau(actor)
    code = validate_code(code)
    name = _validate_name(name)
    description = optional_str(description, "La description", max_len=1000, field="description")
    color = _validate_color(color)
    with write_transaction():
        if any(
            unicodedata.normalize("NFC", existing).casefold() == code.casefold()
            for existing in GameType.objects.values_list("code", flat=True)
        ):
            raise Conflict(
                "Un type de partie possède déjà ce code.",
                fields={"code": code},
            )
        game_type = GameType.objects.create(
            code=code,
            name=name,
            description=description,
            color=color,
        )
        audit.log(
            actor=actor,
            action="gametype.create",
            target=game_type,
            after=audit.snapshot(game_type),
        )
        return game_type


@dataclass(frozen=True)
class TypeDeletionPreview:
    game_type: GameType
    event_count: int


def deletion_preview(type_id, *, actor: Actor):
    _require_bureau(actor)
    with write_transaction():
        game_type = get_type(type_id)
        return TypeDeletionPreview(
            game_type=game_type,
            event_count=Event.objects.filter(game_type_id=game_type.pk).count(),
        )


def delete_type(type_id, *, actor: Actor, confirmation_code, confirm_delete):
    _require_bureau(actor)
    confirmation_code = require_str(
        confirmation_code,
        "Le code de confirmation",
        min_len=1,
        max_len=40,
        field="confirmation_code",
        strip=False,
    )
    if not require_bool(confirm_delete, "Confirmation", field="confirm_delete"):
        raise ValidationFailed(
            "Confirmez explicitement la suppression du type.",
            fields={"confirm_delete": "Confirmation obligatoire."},
        )
    with write_transaction():
        game_type = get_type(type_id)
        if confirmation_code != game_type.code:
            raise ValidationFailed(
                "Recopiez exactement le code du type pour confirmer sa suppression.",
                fields={"confirmation_code": "Le code ne correspond pas."},
            )
        # Recheck inside the write transaction: a date may have started using it.
        if Event.objects.filter(game_type_id=game_type.pk).exists():
            raise Conflict(
                "Ce type est utilisé sur des dates et ne peut pas être supprimé. "
                "Désactivez-le pour le retirer des nouveaux choix sans modifier l'historique."
            )
        audit.log(
            actor=actor,
            action="gametype.delete",
            target=game_type,
            before=audit.snapshot(game_type),
            after=None,
        )
        game_type.delete()


def update_type(type_id, *, actor: Actor, name=None, description=None, color=None):
    _require_bureau(actor)
    game_type = get_type(type_id)
    if name is not None:
        name = _validate_name(name)
    if description is not None:
        description = optional_str(description, "La description", max_len=1000, field="description")
    if color is not None:
        color = _validate_color(color)
    with write_transaction():
        game_type = GameType.objects.get(pk=game_type.pk)
        before = audit.snapshot(game_type)
        name_changed = name is not None and name != game_type.name
        color_changed = color is not None and color != game_type.color
        if name is not None:
            game_type.name = name
        if description is not None:
            game_type.description = description
        if color is not None:
            game_type.color = color
        game_type.save()
        touched_periods = {}
        if name_changed:
            events = Event.objects.select_related("period").filter(
                game_type=game_type, period__archived_at__isnull=True
            )
            for event in events:
                event_before = audit.snapshot(event)
                event.type_label = game_type.name
                event.version += 1
                event.save(update_fields=["type_label", "version", "updated_at"])
                touched_periods[event.period_id] = event.period
                audit.log(
                    actor=actor,
                    action="gametype.relabel",
                    target=event,
                    before=event_before,
                    after=audit.snapshot(event),
                )
            for period in touched_periods.values():
                bump_revision(period)
        if color_changed:
            periods = {
                event.period
                for event in Event.objects.select_related("period").filter(
                    game_type=game_type, period__archived_at__isnull=True
                )
            }
            for period in periods - set(touched_periods.values()):
                bump_revision(period)
        audit.log(
            actor=actor,
            action="gametype.update",
            target=game_type,
            before=before,
            after=audit.snapshot(game_type),
        )
        return game_type


def set_type_active(type_id, *, actor: Actor, active):
    _require_bureau(actor)
    if not isinstance(active, bool):
        raise ValidationFailed("État invalide.", fields={"is_active": "Type invalide."})
    game_type = get_type(type_id)
    with write_transaction():
        game_type = GameType.objects.get(pk=game_type.pk)
        if game_type.is_active == active:
            return game_type
        before = audit.snapshot(game_type)
        game_type.is_active = active
        game_type.save(update_fields=["is_active"])
        audit.log(
            actor=actor,
            action="gametype.activate" if active else "gametype.deactivate",
            target=game_type,
            before=before,
            after=audit.snapshot(game_type),
        )
        return game_type
