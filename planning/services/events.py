from django.db.models import Count, Q

from planning.db import write_transaction
from planning.errors import (
    ClosedConflict,
    Conflict,
    NotFound,
    ValidationFailed,
)
from planning.models import Event, GameType, Period, SiteSettings

from . import audit
from .actors import Actor
from .periods import (
    _check_version,
    _require_bureau,
    _require_not_archived,
    bump_revision,
    get_period,
    paris_today,
)
from .validators import (
    optional_str,
    parse_uuid,
    require_bool,
    require_date,
    require_int,
)

UNSET = object()

MAX_BATCH = 200


def get_event(event_id) -> Event:
    try:
        return Event.objects.select_related("period", "game_type").get(pk=parse_uuid(event_id))
    except Event.DoesNotExist:
        raise NotFound("Date introuvable.") from None


def _resolve_type(game_type_id):
    if game_type_id is None:
        return None
    try:
        game_type = GameType.objects.get(pk=parse_uuid(game_type_id, "Type"))
    except GameType.DoesNotExist:
        raise ValidationFailed(
            "Type de partie inconnu.", fields={"game_type": "Type inconnu."}
        ) from None
    if not game_type.is_active:
        raise ValidationFailed(
            "Ce type de partie est désactivé.",
            fields={"game_type": "Type désactivé."},
        )
    return game_type


def _validate_min_capacity(min_organizers, capacity):
    if min_organizers is not None:
        require_int(min_organizers, "Minimum", minimum=1, field="min_organizers")
    if capacity is not None:
        require_int(capacity, "Capacité", minimum=1, field="capacity")
        effective_min = min_organizers if min_organizers is not None else 1
        if capacity < effective_min:
            raise ValidationFailed(
                "La capacité doit être supérieure ou égale au minimum.",
                fields={"capacity": "Capacité inférieure au minimum."},
            )


def _check_capacity_vs_registrations(event, capacity):
    if capacity is None:
        return
    active = event.registrations.filter(state="active").count()
    if capacity < active:
        raise Conflict(
            "La capacité est inférieure au nombre de bénévoles déjà inscrits ; "
            "personne n'a été retiré.",
            code="capacity_below_registered",
            fields={"active_count": active},
        )


def create_event(
    period_id,
    *,
    actor: Actor,
    date_value,
    game_type_id=None,
    title=None,
    public_note=None,
    min_organizers=None,
    capacity=UNSET,
):
    period = get_period(period_id)
    _require_bureau(actor)
    date_value = require_date(date_value, "Date")
    title = optional_str(title, "Titre", max_len=120, field="title")
    public_note = optional_str(public_note, "Note publique", max_len=1000, field="public_note")
    with write_transaction():
        settings = SiteSettings.get()
        effective_min = (
            min_organizers if min_organizers is not None else settings.default_min_organizers
        )
        effective_capacity = settings.default_capacity if capacity is UNSET else capacity
        _validate_min_capacity(effective_min, effective_capacity)
        period = Period.objects.get(pk=period.pk)
        _require_not_archived(period)
        if not period.start_date <= date_value <= period.end_date:
            raise ValidationFailed(
                "La date doit se situer à l'intérieur des bornes de la période.",
                fields={"date": "Hors bornes de la période."},
            )
        if period.events.filter(date=date_value).exists():
            raise Conflict(
                "Une date existe déjà pour ce jour dans cette période.",
                fields={"date": str(date_value)},
            )
        game_type = _resolve_type(game_type_id)
        event = Event.objects.create(
            period=period,
            date=date_value,
            origin=Event.Origin.MANUAL,
            game_type=game_type,
            type_label=game_type.name if game_type else "",
            title=title,
            public_note=public_note,
            min_organizers=effective_min,
            capacity=effective_capacity,
        )
        bump_revision(period)
        audit.log(
            actor=actor,
            action="event.create",
            target=event,
            after=audit.snapshot(event),
        )
        return event


def update_event(
    event_id,
    *,
    actor: Actor,
    expected_version=None,
    date_value=None,
    game_type_id=UNSET,
    title=None,
    public_note=None,
    min_organizers=None,
    capacity=UNSET,
):
    event = get_event(event_id)
    _require_bureau(actor)
    if expected_version is not None:
        expected_version = require_int(expected_version, "Version", minimum=0, field="version")
    if date_value is not None:
        date_value = require_date(date_value, "Date")
    if title is not None:
        title = optional_str(title, "Titre", max_len=120, field="title")
    if public_note is not None:
        public_note = optional_str(public_note, "Note publique", max_len=1000, field="public_note")
    with write_transaction():
        event = Event.objects.select_related("period").get(pk=event.pk)
        period = event.period
        _require_not_archived(period)
        _check_version(event, expected_version)
        before = audit.snapshot(event)
        if date_value is not None and date_value != event.date:
            if not period.start_date <= date_value <= period.end_date:
                raise ValidationFailed(
                    "La date doit se situer à l'intérieur des bornes de la période.",
                    fields={"date": "Hors bornes de la période."},
                )
            if period.events.exclude(pk=event.pk).filter(date=date_value).exists():
                raise Conflict(
                    "Une autre date existe déjà pour ce jour dans cette période.",
                    fields={"date": str(date_value)},
                )
            event.date = date_value
        if game_type_id is not UNSET:
            requested = parse_uuid(game_type_id, "Type") if game_type_id else None
            if requested != event.game_type_id:
                game_type = _resolve_type(game_type_id)
            else:
                game_type = event.game_type
            event.game_type = game_type
            event.type_label = game_type.name if game_type else ""
        if title is not None:
            event.title = title
        if public_note is not None:
            event.public_note = public_note
        new_min = min_organizers if min_organizers is not None else event.min_organizers
        new_capacity = capacity if capacity is not UNSET else event.capacity
        _validate_min_capacity(new_min, new_capacity)
        if capacity is not UNSET:
            _check_capacity_vs_registrations(event, capacity)
            event.capacity = capacity
        if min_organizers is not None:
            event.min_organizers = min_organizers
        event.version += 1
        event.save()
        bump_revision(period)
        audit.log(
            actor=actor,
            action="event.update",
            target=event,
            before=before,
            after=audit.snapshot(event),
        )
        return event


def set_public_game_type(event_id, *, actor: Actor, game_type_id, expected_version):
    expected_version = require_int(
        expected_version, "Version", minimum=1, field="expected_event_version"
    )
    event = get_event(event_id)
    with write_transaction():
        event = Event.objects.select_related("period").get(pk=event.pk)
        period = event.period
        _require_not_archived(period)
        if event.is_hidden:
            raise ClosedConflict("Cette date n'est plus visible.")
        if event.status != Event.Status.SCHEDULED:
            raise ClosedConflict("Cette date a été annulée par le Bureau.")
        if event.date < paris_today():
            raise ClosedConflict("Cette date est passée ; modification impossible.")
        _check_version(event, expected_version)
        game_type = _resolve_type(game_type_id)
        requested = game_type.pk if game_type is not None else None
        if requested == event.game_type_id:
            return event
        before = audit.snapshot(event)
        event.game_type = game_type
        event.type_label = game_type.name if game_type else ""
        event.version += 1
        event.save(update_fields=["game_type", "type_label", "version", "updated_at"])
        bump_revision(period)
        audit.log(
            actor=actor,
            action="event.type_public",
            target=event,
            before=before,
            after=audit.snapshot(event),
        )
        return event


def cancel_event(event_id, *, actor: Actor, expected_version=None):
    return _set_event_flag(
        event_id,
        actor=actor,
        expected_version=expected_version,
        status=Event.Status.CANCELLED,
        registrations_open=False,
        action="event.cancel",
    )


def restore_event(event_id, *, actor: Actor, expected_version=None):
    return _set_event_flag(
        event_id,
        actor=actor,
        expected_version=expected_version,
        status=Event.Status.SCHEDULED,
        registrations_open=False,
        action="event.restore",
    )


def set_registrations_open(event_id, *, actor: Actor, is_open, expected_version=None):
    is_open = require_bool(is_open, "Ouverture", field="is_open")
    event = get_event(event_id)
    if is_open and event.status == Event.Status.CANCELLED:
        raise ClosedConflict("Une date annulée ne peut pas être rouverte ; restaurez-la d'abord.")
    return _set_event_flag(
        event_id,
        actor=actor,
        expected_version=expected_version,
        status=None,
        registrations_open=is_open,
        action="event.registrations_open" if is_open else "event.registrations_close",
    )


def set_event_hidden(event_id, *, actor: Actor, is_hidden, expected_version):
    is_hidden = require_bool(is_hidden, "Visibilité", field="is_hidden")
    expected_version = require_int(expected_version, "Version", minimum=0, field="version")
    event = get_event(event_id)
    _require_bureau(actor)
    with write_transaction():
        event = Event.objects.select_related("period").get(pk=event.pk)
        period = event.period
        _require_not_archived(period)
        _check_version(event, expected_version)
        before = audit.snapshot(event)
        changed = False
        if event.is_hidden != is_hidden:
            event.is_hidden = is_hidden
            changed = True
        if event.registrations_open and (changed or event.is_hidden):
            event.registrations_open = False
            changed = True
        if changed:
            event.version += 1
            event.save(update_fields=["is_hidden", "registrations_open", "version", "updated_at"])
            bump_revision(period)
            audit.log(
                actor=actor,
                action="event.hide" if is_hidden else "event.unhide",
                target=event,
                before=before,
                after=audit.snapshot(event),
            )
        return event


def _set_event_flag(event_id, *, actor, expected_version, status, registrations_open, action):
    event = get_event(event_id)
    _require_bureau(actor)
    if expected_version is not None:
        expected_version = require_int(expected_version, "Version", minimum=0, field="version")
    with write_transaction():
        event = Event.objects.select_related("period").get(pk=event.pk)
        period = event.period
        _require_not_archived(period)
        _check_version(event, expected_version)
        if registrations_open and event.is_hidden:
            raise ClosedConflict("Cette date est masquée ; réaffichez-la d'abord.")
        changed = False
        before = audit.snapshot(event)
        if status is not None and event.status != status:
            event.status = status
            changed = True
        if registrations_open is not None and (event.registrations_open != registrations_open):
            event.registrations_open = registrations_open
            changed = True
        if changed:
            event.version += 1
            event.save()
            bump_revision(period)
            audit.log(
                actor=actor,
                action=action,
                target=event,
                before=before,
                after=audit.snapshot(event),
            )
        return event


def batch_update(
    event_ids,
    *,
    actor: Actor,
    game_type_id=UNSET,
    min_organizers=None,
    capacity=UNSET,
    registrations_open=None,
    cancel=None,
    expected_versions=None,
):
    _require_bureau(actor)
    ids = list(dict.fromkeys(parse_uuid(eid, "Date") for eid in event_ids))
    checked_versions = {}
    if expected_versions is not None:
        for key, value in expected_versions.items():
            checked_versions[str(parse_uuid(key, "Date"))] = require_int(
                value, "Version", minimum=1, field="version"
            )
        missing_keys = {str(i) for i in ids} - set(checked_versions)
        if missing_keys:
            raise ValidationFailed(
                "Version manquante pour une date sélectionnée ; veuillez recharger.",
                fields={"versions": sorted(missing_keys)},
            )
    if not ids:
        raise ValidationFailed("Aucune date sélectionnée.")
    if len(ids) > MAX_BATCH:
        raise ValidationFailed(
            "200 dates maximum par opération groupée.",
            fields={"events": str(len(ids))},
        )
    if min_organizers is not None:
        require_int(min_organizers, "Minimum", minimum=1, field="min_organizers")
    if capacity is not UNSET and capacity is not None:
        require_int(capacity, "Capacité", minimum=1, field="capacity")
    if registrations_open is not None:
        require_bool(registrations_open, "Ouverture", field="registrations_open")
    if cancel is not None:
        require_bool(cancel, "Annulation", field="cancel")
    with write_transaction():
        events = list(Event.objects.select_related("period").filter(pk__in=ids).order_by("date"))
        if len(events) != len(ids):
            raise NotFound("Certaines dates sont introuvables.")
        for event in events:
            _require_not_archived(event.period)
            expected = checked_versions.get(str(event.pk))
            if expected is not None:
                _check_version(event, expected)
        game_type = _resolve_type(game_type_id) if game_type_id is not UNSET else UNSET
        if min_organizers is not None or capacity is not UNSET:
            counts = {
                row["pk"]: row["n"]
                for row in Event.objects.filter(pk__in=ids)
                .annotate(
                    n=Count(
                        "registrations",
                        filter=Q(registrations__state="active"),
                    )
                )
                .values("pk", "n")
            }
            for event in events:
                eff_min = min_organizers if min_organizers is not None else event.min_organizers
                eff_cap = capacity if capacity is not UNSET else event.capacity
                _validate_min_capacity(eff_min, eff_cap)
                if eff_cap is not None and eff_cap < counts.get(event.pk, 0):
                    raise Conflict(
                        "La capacité est inférieure au nombre de bénévoles déjà "
                        f"inscrits sur le {event.date} ; personne n'a été retiré.",
                        code="capacity_below_registered",
                        fields={"event": str(event.pk)},
                    )
        touched_periods = {}
        updated = []
        skipped = []
        for event in events:
            before = audit.snapshot(event)
            changed = False
            if game_type is not UNSET:
                event.game_type = game_type
                event.type_label = game_type.name if game_type else ""
                changed = True
            if min_organizers is not None and event.min_organizers != min_organizers:
                event.min_organizers = min_organizers
                changed = True
            if capacity is not UNSET and event.capacity != capacity:
                event.capacity = capacity
                changed = True
            if cancel is True and event.status != Event.Status.CANCELLED:
                event.status = Event.Status.CANCELLED
                event.registrations_open = False
                changed = True
            if cancel is False and event.status != Event.Status.SCHEDULED:
                event.status = Event.Status.SCHEDULED
                changed = True
            if registrations_open is not None:
                if event.status == Event.Status.CANCELLED or (
                    registrations_open and event.is_hidden
                ):
                    if event.registrations_open:
                        event.registrations_open = False
                        changed = True
                    if not changed:
                        reason = (
                            "Date annulée : ouverture impossible."
                            if event.status == Event.Status.CANCELLED
                            else "Date masquée : ouverture impossible."
                        )
                        skipped.append({"event_id": str(event.pk), "reason": reason})
                elif event.registrations_open != registrations_open:
                    event.registrations_open = registrations_open
                    changed = True
            if changed:
                event.version += 1
                event.save()
                touched_periods[event.period_id] = event.period
                updated.append(event.pk)
                audit.log(
                    actor=actor,
                    action="event.batch_update",
                    target=event,
                    before=before,
                    after=audit.snapshot(event),
                )
        for period in touched_periods.values():
            bump_revision(period)
        return {"updated": [str(pk) for pk in updated], "skipped": skipped}
