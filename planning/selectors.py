import itertools
from datetime import timedelta

from django.db.models import Count, F, Prefetch, Q

from planning.models import (
    Event,
    GameType,
    Organizer,
    Period,
    Registration,
    SiteSettings,
)

from .errors import ValidationFailed
from .services.organizers import alias_ids
from .services.periods import get_period, paris_today


def default_period(requested_id=None, *, today=None):
    today = today or paris_today()
    if requested_id is not None:
        return get_period(requested_id)
    settings = SiteSettings.get()
    if settings.active_period and not settings.active_period.is_archived:
        return settings.active_period
    next_event = (
        Event.objects.filter(
            status=Event.Status.SCHEDULED,
            is_hidden=False,
            date__gte=today,
            period__archived_at__isnull=True,
        )
        .order_by("date", "period__start_date", "period__created_at")
        .select_related("period")
        .first()
    )
    if next_event is not None:
        return next_event.period
    future = (
        Period.objects.filter(start_date__gte=today, archived_at__isnull=True)
        .order_by("start_date", "created_at")
        .first()
    )
    if future is not None:
        return future
    return Period.objects.order_by("-end_date", "-created_at").first()


def active_count(event):
    return event.registrations.filter(state=Registration.State.ACTIVE).count()


def fill_state(event):
    count = active_count(event)
    if count == 0:
        return "none"
    if count < event.min_organizers:
        return "needs_more"
    return "sufficient"


def period_events(period):
    return (
        period.events.annotate(
            active_registrations=Count(
                "registrations",
                filter=Q(registrations__state=Registration.State.ACTIVE),
            )
        )
        .select_related("game_type", "period")
        .prefetch_related(
            Prefetch(
                "registrations",
                queryset=Registration.objects.filter(state=Registration.State.ACTIVE).order_by(
                    "created_at"
                ),
                to_attr="active_regs",
            )
        )
        .order_by("date")
    )


BUREAU_STAFFING_FILTERS = (
    ("none", "0 inscrit"),
    ("one", "1 inscrit"),
    ("under", "Minimum non atteint"),
    ("all", "Toutes"),
)
BUREAU_STAFFING_VALUES = frozenset(value for value, _ in BUREAU_STAFFING_FILTERS)


def bureau_cleanup_events(period, staffing_filter, *, today=None):
    today = today or paris_today()
    if staffing_filter not in BUREAU_STAFFING_VALUES:
        raise ValidationFailed(
            "Filtre d'inscriptions invalide.",
            fields={"staffing": "Filtre inconnu."},
        )
    qs = period_events(period).filter(
        is_hidden=False,
        status=Event.Status.SCHEDULED,
        date__gte=today,
    )
    if staffing_filter == "none":
        qs = qs.filter(active_registrations=0)
    elif staffing_filter == "one":
        qs = qs.filter(active_registrations=1)
    elif staffing_filter == "under":
        qs = qs.filter(active_registrations__lt=F("min_organizers"))
    return qs


BUREAU_MANAGEMENT_FILTERS = (
    ("all", "Dates à venir"),
    ("none", "0 inscrit"),
    ("one", "1 inscrit"),
    ("under", "Minimum non atteint"),
    ("hidden", "Dates masquées"),
    ("history", "Toutes les dates"),
)
BUREAU_MANAGEMENT_VALUES = frozenset(value for value, _ in BUREAU_MANAGEMENT_FILTERS)


def bureau_management_events(period, staffing_filter, *, today=None):
    today = today or paris_today()
    if staffing_filter in BUREAU_STAFFING_VALUES:
        qs = period_events(period).filter(
            status=Event.Status.SCHEDULED,
            date__gte=today,
        )
        if staffing_filter == "none":
            qs = qs.filter(active_registrations=0)
        elif staffing_filter == "one":
            qs = qs.filter(active_registrations=1)
        elif staffing_filter == "under":
            qs = qs.filter(active_registrations__lt=F("min_organizers"))
        return qs
    if staffing_filter not in BUREAU_MANAGEMENT_VALUES:
        raise ValidationFailed(
            "Filtre d'affichage invalide.",
            fields={"staffing": "Filtre inconnu."},
        )
    if staffing_filter == "hidden":
        return period_events(period).filter(is_hidden=True)
    return period_events(period)


EVENT_FILTERS = {"avenir", "renfort", "toutes"}


def filtered_events(period, filtre=None, *, today=None):
    today = today or paris_today()
    if filtre not in EVENT_FILTERS:
        filtre = "toutes" if period.is_archived else "avenir"
    qs = period_events(period).exclude(is_hidden=True)
    if filtre == "avenir":
        return qs.filter(date__gte=today), filtre
    if filtre == "renfort":
        return (
            qs.filter(
                status=Event.Status.SCHEDULED,
                date__gte=today,
                active_registrations__lt=F("min_organizers"),
            ),
            filtre,
        )
    return qs, filtre


def public_event_interactive(event, *, today=None):
    today = today or paris_today()
    is_archived = event.period.is_archived if hasattr(event, "period") else False
    return (
        event.status == Event.Status.SCHEDULED
        and event.date >= today
        and not is_archived
        and not event.is_hidden
    )


def event_payload(event, today=None):
    today = today or paris_today()
    active_regs = getattr(event, "active_regs", None)
    if active_regs is None:
        active_regs = event.registrations.filter(state=Registration.State.ACTIVE).order_by(
            "created_at"
        )
    regs = [
        {
            "registration_id": str(reg.pk),
            "organizer_id": str(reg.organizer_id),
            "organizer_name": reg.organizer_name,
            "note": reg.note,
            "version": reg.version,
        }
        for reg in active_regs
    ]
    count = len(regs)
    if count == 0:
        fill = "none"
    elif count < event.min_organizers:
        fill = "needs_more"
    else:
        fill = "sufficient"
    is_past = event.date < today
    is_archived = event.period.is_archived if hasattr(event, "period") else False
    interactive = public_event_interactive(event, today=today)
    return {
        "id": str(event.pk),
        "period_id": str(event.period_id),
        "date": event.date.isoformat(),
        "weekday": event.date.weekday(),
        "type_label": event.type_label or "À définir",
        "type_class": event.game_type.color_class if event.game_type_id else "type-c1",
        "game_type_id": str(event.game_type_id) if event.game_type_id else None,
        "title": event.title,
        "public_note": event.public_note,
        "min_organizers": event.min_organizers,
        "capacity": event.capacity,
        "status": event.status,
        "registrations_open": interactive,
        "is_hidden": event.is_hidden,
        "version": event.version,
        "is_past": is_past,
        "is_archived": is_archived,
        "interactive": interactive,
        "active_count": count,
        "fill": fill,
        "organizers": regs,
    }


def organizer_registrations(organizer, *, include_past=False, today=None):
    today = today or paris_today()
    ids = alias_ids(organizer)
    qs = (
        Registration.objects.filter(organizer_id__in=ids)
        .exclude(event__is_hidden=True)
        .select_related("event", "event__period")
        .order_by("event__date")
    )
    if not include_past:
        qs = qs.filter(state=Registration.State.ACTIVE, event__date__gte=today)
    return qs


def public_organizers():
    return Organizer.objects.filter(is_active=True, merged_into__isnull=True).order_by("name")


def planning_context(period, filtre=None):
    events, filtre = filtered_events(period, filtre)
    events = list(events)
    for event in events:
        event.payload = event_payload(event)
    months = [
        {"date": group[0].date, "events": group}
        for group in (
            list(group)
            for _, group in itertools.groupby(events, key=lambda e: (e.date.year, e.date.month))
        )
    ]
    for month in months:
        previous = None
        for event in month["events"]:
            event.weekend_start = previous is not None and not (
                previous.date.weekday() == 5
                and event.date.weekday() == 6
                and event.date - previous.date == timedelta(days=1)
            )
            previous = event
    return {
        "period": period,
        "months": months,
        "game_types": GameType.objects.filter(is_active=True).order_by("name"),
        "described_game_types": GameType.objects.filter(
            Q(is_active=True)
            | Q(pk__in={event.game_type_id for event in events if event.game_type_id})
        )
        .exclude(description="")
        .order_by("name"),
        "filtre": filtre,
    }
