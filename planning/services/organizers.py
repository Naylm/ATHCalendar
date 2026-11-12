import unicodedata

from planning.db import write_transaction
from planning.errors import Conflict, NotFound, ValidationFailed
from planning.models import Organizer, Period, Registration

from . import audit
from .actors import Actor
from .periods import _require_bureau, bump_revision
from .validators import parse_uuid, require_str


def normalize_pseudo(name):
    normalized = unicodedata.normalize("NFKC", name or "")
    return " ".join(normalized.split()).casefold()


def canonical(organizer):
    seen = set()
    while organizer.merged_into_id is not None:
        if organizer.pk in seen:
            break
        seen.add(organizer.pk)
        organizer = organizer.merged_into
    return organizer


def alias_ids(organizer):
    members = {organizer.pk}
    frontier = [organizer.pk]
    while frontier:
        children = set(
            Organizer.objects.filter(merged_into_id__in=frontier).values_list("pk", flat=True)
        )
        children -= members
        members |= children
        frontier = list(children)
    return members


def _validate_name(name):
    return require_str(name, "Le pseudo", min_len=2, max_len=40, field="name")


def _bump_registration_periods(regs):
    for period in Period.objects.filter(
        pk__in={reg.event.period_id for reg in regs}, archived_at__isnull=True
    ):
        bump_revision(period)


def get_or_create_organizer(name, *, actor=None):
    cleaned = _validate_name(name)
    key = normalize_pseudo(cleaned)
    actor = actor or Actor.system()
    with write_transaction():
        organizer = Organizer.objects.filter(normalized_key=key).first()
        if organizer is None:
            organizer = Organizer.objects.create(name=cleaned, normalized_key=key)
            audit.log(
                actor=actor,
                action="organizer.create",
                target=organizer,
                after=audit.snapshot(organizer),
            )
        return canonical(organizer)


def get_organizer(organizer_id) -> Organizer:
    try:
        return Organizer.objects.get(pk=parse_uuid(organizer_id, "Pseudo"))
    except Organizer.DoesNotExist:
        raise NotFound("Pseudo introuvable.") from None


def create_organizer(name, *, actor: Actor):
    _require_bureau(actor)
    return get_or_create_organizer(name, actor=actor)


def rename_organizer(organizer_id, *, actor: Actor, name):
    organizer = get_organizer(organizer_id)
    _require_bureau(actor)
    cleaned = _validate_name(name)
    key = normalize_pseudo(cleaned)
    with write_transaction():
        organizer = Organizer.objects.get(pk=organizer.pk)
        if Organizer.objects.filter(normalized_key=key).exclude(pk=organizer.pk).exists():
            raise Conflict(
                "Un autre pseudo possède déjà ce nom après normalisation.",
                fields={"name": cleaned},
            )
        before = audit.snapshot(organizer)
        organizer.name = cleaned
        organizer.normalized_key = key
        organizer.save(update_fields=["name", "normalized_key", "updated_at"])
        regs = [
            reg
            for reg in organizer.registrations.select_related("event", "event__period")
            if not reg.event.period.is_archived
        ]
        Registration.objects.filter(pk__in=[r.pk for r in regs]).update(organizer_name=cleaned)
        _bump_registration_periods(regs)
        audit.log(
            actor=actor,
            action="organizer.rename",
            target=organizer,
            before=before,
            after=audit.snapshot(organizer),
        )
        return organizer


def set_organizer_active(organizer_id, *, actor: Actor, active):
    organizer = get_organizer(organizer_id)
    _require_bureau(actor)
    with write_transaction():
        organizer = Organizer.objects.get(pk=organizer.pk)
        if active and organizer.merged_into_id is not None:
            raise ValidationFailed("Un pseudo fusionné ne peut pas être réactivé.")
        if organizer.is_active != active:
            before = audit.snapshot(organizer)
            organizer.is_active = active
            organizer.save(update_fields=["is_active", "updated_at"])
            regs = list(organizer.registrations.select_related("event"))
            _bump_registration_periods(regs)
            audit.log(
                actor=actor,
                action="organizer.activate" if active else "organizer.deactivate",
                target=organizer,
                before=before,
                after=audit.snapshot(organizer),
            )
        return organizer


def merge_organizers(source_id, target_id, *, actor: Actor, note_choices=None):
    source = get_organizer(source_id)
    target = get_organizer(target_id)
    _require_bureau(actor)
    note_choices = note_choices or {}
    validated_choices = {}
    for key, value in note_choices.items():
        validated_choices[str(parse_uuid(key, "Date"))] = require_str(
            value, "La note finale", max_len=500, min_len=0, field="note", strip=False
        )
    with write_transaction():
        source = Organizer.objects.get(pk=source.pk)
        target = canonical(Organizer.objects.get(pk=target.pk))
        if source.merged_into_id is not None:
            raise ValidationFailed("Ce pseudo est déjà fusionné ; choisissez son profil canonique.")
        if source.pk == target.pk:
            raise ValidationFailed("Impossible de fusionner un pseudo avec lui-même.")
        target_regs = {reg.event_id: reg for reg in Registration.objects.filter(organizer=target)}
        conflicts = []
        movable = []
        source_regs = list(
            Registration.objects.select_related("event", "event__period").filter(organizer=source)
        )
        for reg in source_regs:
            if reg.event.period.is_archived:
                continue
            existing = target_regs.get(reg.event_id)
            if existing is None:
                movable.append(reg)
            elif existing.note != reg.note and str(reg.event_id) not in validated_choices:
                conflicts.append(reg.event_id)
        if conflicts:
            raise Conflict(
                "Des notes différentes existent sur des dates communes ; choisir le texte final.",
                code="merge_note_conflict",
                fields={
                    "conflicts": [
                        {
                            "event_id": str(eid),
                            "source_note": next(r.note for r in source_regs if r.event_id == eid),
                            "target_note": target_regs[eid].note,
                        }
                        for eid in conflicts
                    ]
                },
            )
        touched_periods = {}
        for reg in movable:
            before = audit.snapshot(reg)
            reg.organizer = target
            reg.version += 1
            reg.save(update_fields=["organizer", "version", "updated_at"])
            touched_periods[reg.event.period_id] = reg.event.period
            audit.log(
                actor=actor,
                action="organizer.merge_move",
                target=reg,
                before=before,
                after=audit.snapshot(reg),
            )
        for reg in source_regs:
            if reg.event.period.is_archived:
                continue
            existing = target_regs.get(reg.event_id)
            if existing is None:
                continue
            final_note = validated_choices.get(str(reg.event_id), existing.note)
            changed = False
            before = audit.snapshot(existing)
            if (
                existing.state != Registration.State.ACTIVE
                and reg.state == Registration.State.ACTIVE
            ):
                existing.state = Registration.State.ACTIVE
                changed = True
            if final_note != existing.note:
                existing.note = final_note
                changed = True
            if changed:
                existing.version += 1
                existing.save(update_fields=["state", "note", "version", "updated_at"])
            if reg.state != Registration.State.WITHDRAWN:
                reg.state = Registration.State.WITHDRAWN
                reg.version += 1
                reg.save(update_fields=["state", "version", "updated_at"])
            touched_periods[reg.event.period_id] = reg.event.period
            audit.log(
                actor=actor,
                action="organizer.merge_consolidate",
                target=existing,
                before=before,
                after=audit.snapshot(existing),
            )
        descendants = Organizer.objects.filter(merged_into=source)
        for descendant in descendants:
            descendant.merged_into = target
            descendant.save(update_fields=["merged_into", "updated_at"])
            audit.log(
                actor=actor,
                action="organizer.alias_retarget",
                target=descendant,
                after=audit.snapshot(descendant),
            )
        before = audit.snapshot(source)
        source.is_active = False
        source.merged_into = target
        source.save(update_fields=["is_active", "merged_into", "updated_at"])
        audit.log(
            actor=actor,
            action="organizer.merge",
            target=source,
            before=before,
            after=audit.snapshot(source),
        )
        for period in touched_periods.values():
            bump_revision(period)
        return target
