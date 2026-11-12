from planning.db import write_transaction
from planning.errors import (
    CapacityConflict,
    ClosedConflict,
    Conflict,
    NotFound,
    ValidationFailed,
    VersionConflict,
)
from planning.models import Event, Registration

from . import audit
from .actors import Actor
from .events import MAX_BATCH, get_event
from .organizers import canonical, get_organizer
from .periods import _require_not_archived, bump_revision, paris_today
from .validators import optional_str, parse_uuid

ACTIVE = Registration.State.ACTIVE
WITHDRAWN = Registration.State.WITHDRAWN


def _public_write_allowed(event, today):
    if event.status != Event.Status.SCHEDULED:
        raise ClosedConflict("Cette date a été annulée par le Bureau.")
    if not event.registrations_open:
        raise ClosedConflict("Les inscriptions sont fermées pour cette date.")
    if event.date < today:
        raise ClosedConflict("Cette date est passée ; modification impossible.")


def _check_capacity(event, exclude_registration_id=None):
    if event.capacity is None:
        return
    qs = event.registrations.filter(state=ACTIVE)
    if exclude_registration_id is not None:
        qs = qs.exclude(pk=exclude_registration_id)
    if qs.count() >= event.capacity:
        raise CapacityConflict("Cette date est complète.")


def set_registration(
    event_id,
    organizer_id,
    desired_state,
    *,
    actor: Actor,
    note=None,
    expected_version=None,
    expected_event_version=None,
):
    if desired_state not in (ACTIVE, WITHDRAWN):
        raise ValidationFailed(
            "État demandé invalide.", fields={"state": str(desired_state)}
        )
    if note is not None:
        note = optional_str(note, "La note", max_len=500, field="note")
    event = get_event(event_id)
    organizer = get_organizer(organizer_id)
    today = paris_today()
    with write_transaction():
        event = Event.objects.select_related("period").get(pk=event.pk)
        organizer = canonical(type(organizer).objects.get(pk=organizer.pk))
        _require_not_archived(event.period)
        if not actor.is_bureau:
            _public_write_allowed(event, today)
            if not organizer.is_active:
                raise ClosedConflict("Ce pseudo est désactivé.")
        if (
            expected_event_version is not None
            and event.version != expected_event_version
        ):
            raise VersionConflict(
                "Cette date a été modifiée entre-temps ; veuillez recharger.",
                fields={"event_version": str(event.version)},
            )
        registration = Registration.objects.filter(
            event=event, organizer=organizer
        ).first()
        new_state = desired_state
        new_note = (
            note if note is not None else (registration.note if registration else "")
        )
        if registration is not None and expected_version is not None:
            needs_change = (
                registration.state != new_state or registration.note != new_note
            )
            if needs_change and registration.version != expected_version:
                raise VersionConflict(
                    "Cette inscription a été modifiée entre-temps ; "
                    "veuillez recharger.",
                    fields={"version": str(registration.version)},
                )
        if registration is not None and (
            registration.state == new_state
            and (note is None or registration.note == new_note)
        ):
            return registration
        if new_state == ACTIVE:
            _check_capacity(
                event,
                exclude_registration_id=registration.pk if registration else None,
            )
        before = audit.snapshot(registration)
        existed = registration is not None
        if registration is None:
            registration = Registration(
                event=event, organizer=organizer, organizer_name=organizer.name
            )
        registration.state = new_state
        if note is not None:
            registration.note = note
        registration.organizer_name = organizer.name
        if existed:
            registration.version += 1
        registration.save()
        bump_revision(event.period)
        audit.log(
            actor=actor,
            action="registration.set",
            target=registration,
            before=before,
            after=audit.snapshot(registration),
        )
        return registration


def batch_set(
    event_ids,
    organizer_id,
    desired_state,
    *,
    actor: Actor,
    note=None,
    versions=None,
):
    ids = list(dict.fromkeys(parse_uuid(eid, "Date") for eid in event_ids))
    if not ids:
        raise ValidationFailed("Aucune date sélectionnée.")
    if len(ids) > MAX_BATCH:
        raise ValidationFailed(
            "200 dates maximum par opération groupée.",
            fields={"events": str(len(ids))},
        )
    versions = versions or {}
    if note is not None:
        note = optional_str(note, "La note", max_len=500, field="note")
    organizer = get_organizer(organizer_id)
    today = paris_today()
    failures = {}
    with write_transaction():
        organizer = canonical(type(organizer).objects.get(pk=organizer.pk))
        events = {
            e.pk: e
            for e in Event.objects.select_related("period").filter(pk__in=ids)
        }
        if len(events) != len(ids):
            raise NotFound("Certaines dates sont introuvables.")
        for event_id in ids:
            event = events[event_id]
            if event.period.is_archived:
                failures[str(event_id)] = "Période archivée."
                continue
            version_info = versions.get(str(event_id), {}) or {}
            expected_event_version = version_info.get("event")
            if (
                expected_event_version is not None
                and event.version != expected_event_version
            ):
                raise VersionConflict(
                    f"La date du {event.date} a été modifiée entre-temps ; "
                    "veuillez recharger.",
                    fields={"event_id": str(event_id)},
                )
            if not actor.is_bureau:
                try:
                    _public_write_allowed(event, today)
                except ClosedConflict as exc:
                    failures[str(event_id)] = exc.message
        if not actor.is_bureau and not organizer.is_active:
            raise ClosedConflict("Ce pseudo est désactivé.")
        if failures:
            raise Conflict(
                "Certaines dates ne peuvent pas être modifiées ; "
                "rien n'a été appliqué.",
                fields={"failures": failures},
            )
        results = []
        for event_id in ids:
            version_info = versions.get(str(event_id), {}) or {}
            results.append(
                set_registration(
                    event_id,
                    organizer.pk,
                    desired_state,
                    actor=actor,
                    note=note,
                    expected_version=version_info.get("registration"),
                    expected_event_version=version_info.get("event"),
                )
            )
        return results
