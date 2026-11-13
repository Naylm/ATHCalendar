from datetime import timedelta

from django.db import IntegrityError
from django.utils import timezone

from planning.db import write_transaction
from planning.errors import (
    ArchivedConflict,
    Conflict,
    Forbidden,
    NotFound,
    ValidationFailed,
    VersionConflict,
)
from planning.models import Event, Period, SiteSettings

from . import audit
from .actors import Actor
from .validators import (
    parse_uuid,
    require_bool,
    require_date,
    require_int,
    require_str,
)

WEEKEND_DAYS = (5, 6)
MAX_CLEANUP = 200


def paris_today():
    return timezone.localdate()


def weekend_dates(start_date, end_date):
    require_date(start_date, "Début")
    require_date(end_date, "Fin")
    if start_date > end_date:
        raise ValidationFailed(
            "La date de début doit précéder ou égaler la date de fin.",
            fields={"start_date": "Début après la fin."},
        )
    days = []
    current = start_date
    while True:
        if current.weekday() in WEEKEND_DAYS:
            days.append(current)
        if current >= end_date:
            break
        current += timedelta(days=1)
    return days


def _overlapping_periods(start_date, end_date, exclude_id=None):
    qs = Period.objects.filter(start_date__lte=end_date, end_date__gte=start_date)
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    return qs


def preview_period(*, start_date, end_date):
    days = weekend_dates(start_date, end_date)
    others = list(_overlapping_periods(start_date, end_date))
    overlaps = [
        {
            "date": day,
            "periods": [p.label for p in others if p.start_date <= day <= p.end_date],
        }
        for day in days
    ]
    overlaps = [entry for entry in overlaps if entry["periods"]]
    return {"dates": days, "total": len(days), "overlaps": overlaps}


def get_period(period_id) -> Period:
    try:
        return Period.objects.get(pk=parse_uuid(period_id))
    except Period.DoesNotExist:
        raise NotFound("Période introuvable.") from None


def _require_bureau(actor):
    if not actor.is_bureau:
        raise Forbidden("Action réservée au Bureau.")


def _require_not_archived(period):
    if period.is_archived:
        raise ArchivedConflict(
            "La période est archivée ; une réouverture explicite est nécessaire."
        )


def _check_version(instance, expected_version):
    if expected_version is not None and instance.version != expected_version:
        raise VersionConflict(
            "Ces données ont été modifiées entre-temps ; veuillez recharger.",
            fields={"version": str(instance.version)},
        )


def bump_revision(period):
    period.revision += 1
    period.save(update_fields=["revision", "updated_at"])


def _validated_label(label):
    return require_str(label, "Le libellé de période", min_len=1, max_len=120, field="label")


def create_period(
    *,
    actor: Actor,
    label,
    start_date,
    end_date,
    creation_key,
    selected_dates=None,
    confirm_overlap=False,
):
    _require_bureau(actor)
    label = _validated_label(label)
    require_date(start_date, "Début")
    require_date(end_date, "Fin")
    creation_key = parse_uuid(creation_key, "Clé de création")
    confirm_overlap = require_bool(confirm_overlap, "Confirmation", field="confirm")
    candidate_days = weekend_dates(start_date, end_date)
    if selected_dates is not None:
        chosen = sorted({require_date(d, "Date") for d in selected_dates})
        if not set(chosen).issubset(set(candidate_days)):
            raise ValidationFailed(
                "Certaines dates sélectionnées ne sont pas des week-ends de l'intervalle.",
                fields={"dates": "Dates hors week-ends de la période."},
            )
    else:
        chosen = candidate_days
    with write_transaction():
        existing = Period.objects.filter(creation_key=creation_key).first()
        if existing is not None:
            return existing
        overlaps = preview_period(start_date=start_date, end_date=end_date)["overlaps"]
        if overlaps and not confirm_overlap:
            raise Conflict(
                "Cette plage recouvre une période existante ; "
                "une confirmation explicite est requise.",
                code="overlap",
                fields={
                    "overlaps": [
                        {"date": str(o["date"]), "periods": o["periods"]} for o in overlaps
                    ]
                },
            )
        try:
            period = Period.objects.create(
                creation_key=creation_key,
                label=label,
                start_date=start_date,
                end_date=end_date,
            )
        except IntegrityError:
            return Period.objects.get(creation_key=creation_key)
        settings = SiteSettings.get()
        Event.objects.bulk_create(
            [
                Event(
                    period=period,
                    date=day,
                    origin=Event.Origin.GENERATED,
                    min_organizers=settings.default_min_organizers,
                    capacity=settings.default_capacity,
                )
                for day in chosen
            ]
        )
        bump_revision(period)
        audit.log(
            actor=actor,
            action="period.create",
            target=period,
            after=audit.snapshot(period),
        )
        return period


def missing_weekend_dates(period):
    existing = set(period.events.values_list("date", flat=True))
    return [day for day in weekend_dates(period.start_date, period.end_date) if day not in existing]


def complete_missing_weekends(period_id, *, actor: Actor, selected_dates=None):
    period = get_period(period_id)
    _require_bureau(actor)
    if selected_dates is not None:
        selected_dates = {require_date(d, "Date") for d in selected_dates}
    with write_transaction():
        period = Period.objects.get(pk=period.pk)
        _require_not_archived(period)
        existing = set(period.events.values_list("date", flat=True))
        missing = [
            day for day in weekend_dates(period.start_date, period.end_date) if day not in existing
        ]
        if selected_dates is not None:
            if not selected_dates.issubset(set(missing)):
                raise ValidationFailed(
                    "Certaines dates sélectionnées ne sont pas des week-ends "
                    "manquants de cette période.",
                    fields={"dates": "Sélection invalide."},
                )
            missing = sorted(selected_dates)
        if missing:
            settings = SiteSettings.get()
            Event.objects.bulk_create(
                [
                    Event(
                        period=period,
                        date=day,
                        origin=Event.Origin.GENERATED,
                        min_organizers=settings.default_min_organizers,
                        capacity=settings.default_capacity,
                    )
                    for day in missing
                ]
            )
            bump_revision(period)
            audit.log(
                actor=actor,
                action="period.generate_missing",
                target=period,
                after={"created_dates": [str(d) for d in missing]},
            )
        return missing


def update_period(
    period_id,
    *,
    actor: Actor,
    expected_version=None,
    label=None,
    start_date=None,
    end_date=None,
):
    period = get_period(period_id)
    _require_bureau(actor)
    if expected_version is not None:
        expected_version = require_int(expected_version, "Version", minimum=0, field="version")
    if label is not None:
        label = _validated_label(label)
    if start_date is not None:
        require_date(start_date, "Début")
    if end_date is not None:
        require_date(end_date, "Fin")
    with write_transaction():
        period = Period.objects.get(pk=period.pk)
        _require_not_archived(period)
        _check_version(period, expected_version)
        before = audit.snapshot(period)
        if label is not None:
            period.label = label
        new_start = start_date if start_date is not None else period.start_date
        new_end = end_date if end_date is not None else period.end_date
        if new_start > new_end:
            raise ValidationFailed(
                "La date de début doit précéder ou égaler la date de fin.",
                fields={"start_date": "Début après la fin."},
            )
        excluded = sorted(
            period.events.filter(date__lt=new_start).values_list("date", flat=True)
        ) + sorted(period.events.filter(date__gt=new_end).values_list("date", flat=True))
        if excluded:
            raise Conflict(
                "Ces bornes excluraient des dates existantes ; aucune date n'a été perdue.",
                code="bounds_exclude_events",
                fields={"excluded_dates": [str(d) for d in excluded]},
            )
        period.start_date = new_start
        period.end_date = new_end
        period.version += 1
        bump_revision(period)
        period.save()
        audit.log(
            actor=actor,
            action="period.update",
            target=period,
            before=before,
            after=audit.snapshot(period),
        )
        return period


def set_active(period_id, *, actor: Actor):
    period = get_period(period_id)
    _require_bureau(actor)
    with write_transaction():
        period = Period.objects.get(pk=period.pk)
        _require_not_archived(period)
        settings = SiteSettings.get()
        if settings.active_period_id != period.pk:
            settings.active_period = period
            settings.save(update_fields=["active_period"])
            audit.log(
                actor=actor,
                action="period.set_active",
                target=period,
                after=audit.snapshot(period),
            )
        return period


def archive(period_id, *, actor: Actor):
    period = get_period(period_id)
    _require_bureau(actor)
    with write_transaction():
        period = Period.objects.get(pk=period.pk)
        if not period.is_archived:
            before = audit.snapshot(period)
            period.archived_at = timezone.now()
            period.version += 1
            period.revision += 1
            period.save(update_fields=["archived_at", "version", "revision", "updated_at"])
            settings = SiteSettings.get()
            if settings.active_period_id == period.pk:
                settings.active_period = None
                settings.save(update_fields=["active_period"])
            audit.log(
                actor=actor,
                action="period.archive",
                target=period,
                before=before,
                after=audit.snapshot(period),
            )
        return period


def reopen(period_id, *, actor: Actor):
    period = get_period(period_id)
    _require_bureau(actor)
    with write_transaction():
        period = Period.objects.get(pk=period.pk)
        if period.is_archived:
            before = audit.snapshot(period)
            period.archived_at = None
            period.version += 1
            period.revision += 1
            period.save(update_fields=["archived_at", "version", "revision", "updated_at"])
            audit.log(
                actor=actor,
                action="period.reopen",
                target=period,
                before=before,
                after=audit.snapshot(period),
            )
        return period


def set_period_registrations(period_id, *, actor: Actor, is_open):
    period = get_period(period_id)
    _require_bureau(actor)
    is_open = require_bool(is_open, "Ouverture", field="is_open")
    with write_transaction():
        period = Period.objects.get(pk=period.pk)
        _require_not_archived(period)
        changed_qs = period.events.filter(status=Event.Status.SCHEDULED).exclude(
            registrations_open=is_open
        )
        if is_open:
            changed_qs = changed_qs.exclude(is_hidden=True)
        changed = list(changed_qs)
        for event in changed:
            event.registrations_open = is_open
            event.version += 1
            event.save(update_fields=["registrations_open", "version", "updated_at"])
        if changed:
            bump_revision(period)
        audit.log(
            actor=actor,
            action=("period.registrations_open" if is_open else "period.registrations_close"),
            target=period,
            after={"changed_events": [str(e.pk) for e in changed]},
        )
        return period


def finalize_period(period_id, *, actor: Actor, keep_event_ids, expected_revision):
    period = get_period(period_id)
    _require_bureau(actor)
    if isinstance(expected_revision, bool):
        raise ValidationFailed(
            "Révision invalide.", fields={"expected_revision": "Entier attendu."}
        )
    expected_revision = require_int(
        expected_revision, "Révision", minimum=0, field="expected_revision"
    )
    if not isinstance(keep_event_ids, (list, tuple, set)):
        raise ValidationFailed(
            "Sélection de dates invalide.", fields={"keep_event_ids": "Liste attendue."}
        )
    keep = {parse_uuid(value, "Date") for value in keep_event_ids}
    with write_transaction():
        period = Period.objects.get(pk=period.pk)
        _require_not_archived(period)
        if period.revision != expected_revision:
            raise VersionConflict(
                "Le planning a été modifié entre-temps ; veuillez recharger.",
                fields={"revision": str(period.revision)},
            )
        future_events = list(period.events.filter(date__gte=paris_today()).order_by("date"))
        candidates = {event.pk for event in future_events if event.status == Event.Status.SCHEDULED}
        invalid = keep - candidates
        if invalid:
            raise ValidationFailed(
                "La sélection contient des dates invalides ; rien n'a été modifié.",
                fields={"keep_event_ids": sorted(str(pk) for pk in invalid)},
            )
        changed = []
        for event in future_events:
            new_hidden = event.pk not in keep
            if event.is_hidden == new_hidden and not event.registrations_open:
                continue
            before = audit.snapshot(event)
            event.registrations_open = False
            event.is_hidden = new_hidden
            event.version += 1
            event.save(update_fields=["registrations_open", "is_hidden", "version", "updated_at"])
            changed.append(event)
            audit.log(
                actor=actor,
                action="event.finalize",
                target=event,
                before=before,
                after=audit.snapshot(event),
            )
        if changed:
            bump_revision(period)
            audit.log(
                actor=actor,
                action="period.finalize",
                target=period,
                after={
                    "keep_event_ids": sorted(str(pk) for pk in keep),
                    "hidden_event_ids": sorted(
                        str(event.pk) for event in future_events if event.is_hidden
                    ),
                },
            )
        return period


def hide_selected_dates(period_id, *, actor: Actor, event_ids, staffing_filter, expected_revision):
    from planning import selectors

    period = get_period(period_id)
    _require_bureau(actor)
    if staffing_filter not in selectors.BUREAU_STAFFING_VALUES:
        raise ValidationFailed(
            "Filtre d'inscriptions invalide.", fields={"staffing": "Filtre inconnu."}
        )
    if isinstance(expected_revision, bool):
        raise ValidationFailed(
            "Révision invalide.", fields={"expected_revision": "Entier attendu."}
        )
    expected_revision = require_int(
        expected_revision, "Révision", minimum=0, field="expected_revision"
    )
    if not isinstance(event_ids, (list, tuple, set)):
        raise ValidationFailed(
            "Sélection de dates invalide.", fields={"event_ids": "Liste attendue."}
        )
    ids = list(dict.fromkeys(parse_uuid(value, "Date") for value in event_ids))
    if not ids:
        raise ValidationFailed("Aucune date sélectionnée.", fields={"event_ids": "Vide."})
    if len(ids) > MAX_CLEANUP:
        raise ValidationFailed(
            "200 dates maximum par opération groupée.",
            fields={"event_ids": str(len(ids))},
        )
    with write_transaction():
        period = Period.objects.get(pk=period.pk)
        _require_not_archived(period)
        if period.revision != expected_revision:
            raise VersionConflict(
                "Le planning a été modifié entre-temps ; veuillez recharger.",
                fields={"revision": str(period.revision)},
            )
        candidates = {
            event.pk for event in selectors.bureau_cleanup_events(period, staffing_filter)
        }
        invalid = set(ids) - candidates
        if invalid:
            raise ValidationFailed(
                "La sélection contient des dates non éligibles ; rien n'a été modifié.",
                fields={"event_ids": sorted(str(pk) for pk in invalid)},
            )
        changed = list(Event.objects.filter(pk__in=ids).order_by("date"))
        for event in changed:
            before = audit.snapshot(event)
            event.is_hidden = True
            event.registrations_open = False
            event.version += 1
            event.save(update_fields=["is_hidden", "registrations_open", "version", "updated_at"])
            audit.log(
                actor=actor,
                action="event.hide",
                target=event,
                before=before,
                after=audit.snapshot(event),
            )
        if changed:
            bump_revision(period)
            audit.log(
                actor=actor,
                action="period.cleanup",
                target=period,
                after={
                    "staffing": staffing_filter,
                    "hidden_event_ids": sorted(str(event.pk) for event in changed),
                },
            )
        return changed


def close_selected_dates(period_id, *, actor: Actor, event_ids, staffing_filter, expected_revision):
    from planning import selectors
    from planning.services import events as event_services

    period = get_period(period_id)
    _require_bureau(actor)
    if staffing_filter not in selectors.BUREAU_STAFFING_VALUES:
        raise ValidationFailed(
            "Filtre d'inscriptions invalide.", fields={"staffing": "Filtre inconnu."}
        )
    if isinstance(expected_revision, bool):
        raise ValidationFailed(
            "Révision invalide.", fields={"expected_revision": "Entier attendu."}
        )
    expected_revision = require_int(
        expected_revision, "Révision", minimum=0, field="expected_revision"
    )
    if not isinstance(event_ids, (list, tuple, set)):
        raise ValidationFailed(
            "Sélection de dates invalide.", fields={"event_ids": "Liste attendue."}
        )
    ids = list(dict.fromkeys(parse_uuid(value, "Date") for value in event_ids))
    if not ids:
        raise ValidationFailed("Aucune date sélectionnée.", fields={"event_ids": "Vide."})
    if len(ids) > MAX_CLEANUP:
        raise ValidationFailed(
            "200 dates maximum par opération groupée.",
            fields={"event_ids": str(len(ids))},
        )
    with write_transaction():
        period = Period.objects.get(pk=period.pk)
        _require_not_archived(period)
        if period.revision != expected_revision:
            raise VersionConflict(
                "Le planning a été modifié entre-temps ; veuillez recharger.",
                fields={"revision": str(period.revision)},
            )
        candidates = {
            event.pk for event in selectors.bureau_cleanup_events(period, staffing_filter)
        }
        invalid = set(ids) - candidates
        if invalid:
            raise ValidationFailed(
                "La sélection contient des dates non éligibles ; rien n'a été modifié.",
                fields={"event_ids": sorted(str(pk) for pk in invalid)},
            )
        versions = {str(event.pk): event.version for event in Event.objects.filter(pk__in=ids)}
        result = event_services.batch_update(
            ids,
            actor=actor,
            registrations_open=False,
            expected_versions=versions,
        )
        return result["updated"]


def unhide_selected_dates(period_id, *, actor: Actor, event_ids, expected_revision):
    period = get_period(period_id)
    _require_bureau(actor)
    if isinstance(expected_revision, bool):
        raise ValidationFailed(
            "Révision invalide.", fields={"expected_revision": "Entier attendu."}
        )
    expected_revision = require_int(
        expected_revision, "Révision", minimum=0, field="expected_revision"
    )
    if not isinstance(event_ids, (list, tuple, set)):
        raise ValidationFailed(
            "Sélection de dates invalide.", fields={"event_ids": "Liste attendue."}
        )
    ids = list(dict.fromkeys(parse_uuid(value, "Date") for value in event_ids))
    if not ids:
        raise ValidationFailed("Aucune date sélectionnée.", fields={"event_ids": "Vide."})
    if len(ids) > MAX_CLEANUP:
        raise ValidationFailed(
            "200 dates maximum par opération groupée.",
            fields={"event_ids": str(len(ids))},
        )
    with write_transaction():
        period = Period.objects.get(pk=period.pk)
        _require_not_archived(period)
        if period.revision != expected_revision:
            raise VersionConflict(
                "Le planning a été modifié entre-temps ; veuillez recharger.",
                fields={"revision": str(period.revision)},
            )
        changed = list(
            Event.objects.filter(pk__in=ids, period=period, is_hidden=True).order_by("date")
        )
        found = {event.pk for event in changed}
        invalid = set(ids) - found
        if invalid:
            raise ValidationFailed(
                "La sélection contient des dates non éligibles ; rien n'a été modifié.",
                fields={"event_ids": sorted(str(pk) for pk in invalid)},
            )
        for event in changed:
            before = audit.snapshot(event)
            event.is_hidden = False
            event.registrations_open = False
            event.version += 1
            event.save(update_fields=["is_hidden", "registrations_open", "version", "updated_at"])
            audit.log(
                actor=actor,
                action="event.unhide",
                target=event,
                before=before,
                after=audit.snapshot(event),
            )
        bump_revision(period)
        return changed


def set_date_visibility(
    period_id, event_id, *, actor: Actor, is_visible, expected_revision, expected_version
):
    from planning.services import events as event_services

    period = get_period(period_id)
    _require_bureau(actor)
    is_visible = require_bool(is_visible, "Visibilité", field="visible")
    if isinstance(expected_revision, bool):
        raise ValidationFailed(
            "Révision invalide.", fields={"expected_revision": "Entier attendu."}
        )
    expected_revision = require_int(
        expected_revision, "Révision", minimum=0, field="expected_revision"
    )
    if isinstance(expected_version, bool):
        raise ValidationFailed("Version invalide.", fields={"expected_version": "Entier attendu."})
    expected_version = require_int(expected_version, "Version", minimum=1, field="expected_version")
    event_pk = parse_uuid(event_id, "Date")
    with write_transaction():
        period = Period.objects.get(pk=period.pk)
        _require_not_archived(period)
        if period.revision != expected_revision:
            raise VersionConflict(
                "Le planning a été modifié entre-temps ; veuillez recharger.",
                fields={"revision": str(period.revision)},
            )
        if not Event.objects.filter(pk=event_pk, period=period).exists():
            raise NotFound("Date introuvable.")
        return event_services.set_event_hidden(
            event_pk,
            actor=actor,
            is_hidden=not is_visible,
            expected_version=expected_version,
        )


def set_default_min_organizers(value, *, actor: Actor):
    _require_bureau(actor)
    value = require_int(value, "Minimum par défaut", minimum=1, field="min")
    settings = SiteSettings.get()
    settings.default_min_organizers = value
    settings.save(update_fields=["default_min_organizers"])
    return settings


def update_settings(*, actor: Actor, site_name, default_min_organizers, default_capacity):
    _require_bureau(actor)
    site_name = require_str(site_name, "Le nom du site", min_len=1, max_len=120, field="site_name")
    default_min_organizers = require_int(
        default_min_organizers, "Le minimum par défaut", minimum=1, field="default_min_organizers"
    )
    if default_capacity is not None:
        default_capacity = require_int(
            default_capacity,
            "La capacité par défaut",
            minimum=default_min_organizers,
            field="default_capacity",
        )
    with write_transaction():
        settings = SiteSettings.objects.get(pk=SiteSettings.get().pk)
        before = audit.snapshot(settings)
        settings.site_name = site_name
        settings.default_min_organizers = default_min_organizers
        settings.default_capacity = default_capacity
        settings.save()
        audit.log(
            actor=actor,
            action="settings.update",
            target=settings,
            before=before,
            after=audit.snapshot(settings),
        )
        return settings
