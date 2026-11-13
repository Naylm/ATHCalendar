from dataclasses import dataclass
from datetime import date

from django.db import transaction
from django.db.models import Prefetch

from .models import Event, Organizer, Period, Registration


@dataclass(frozen=True)
class PlanningRow:
    event_id: str
    date: date
    type_label: str
    title: str
    status: str
    availability: str
    hidden: bool
    minimum: int
    capacity: int | None
    active_count: int
    organizer_names: tuple[str, ...]
    public_note: str


@dataclass(frozen=True)
class RegistrationRow:
    event_id: str
    date: date
    type_label: str
    hidden: bool
    organizer_name: str
    state: str
    note: str


@dataclass(frozen=True)
class PeriodTable:
    period_id: str
    label: str
    start_date: date
    end_date: date
    archived: bool
    planning: tuple[PlanningRow, ...]
    registrations: tuple[RegistrationRow, ...]


@dataclass(frozen=True)
class RecapDate:
    event_id: str
    date: date
    type_label: str
    cancelled: bool
    availability: str


@dataclass(frozen=True)
class RecapPerson:
    organizer_id: str
    name: str
    dates: tuple[RecapDate, ...]

    @property
    def registration_count(self):
        return len(self.dates)


def _availability_label(event, active_count):
    if (
        event.availability == Event.Availability.AVAILABLE
        and event.capacity is not None
        and active_count >= event.capacity
    ):
        return "Complète (capacité atteinte)"
    return event.get_availability_display()


def period_table(period):
    """Bureau export: all dates and registration states, including hidden history."""
    with transaction.atomic():
        period = Period.objects.get(pk=period.pk)
        events = (
            Event.objects.filter(period=period)
            .order_by("date", "pk")
            .prefetch_related(
                Prefetch(
                    "registrations",
                    queryset=Registration.objects.order_by("created_at", "pk"),
                    to_attr="report_regs",
                )
            )
        )
        planning_rows = []
        registration_rows = []
        for event in events:
            active = [reg for reg in event.report_regs if reg.state == Registration.State.ACTIVE]
            type_label = event.type_label or "À définir"
            planning_rows.append(
                PlanningRow(
                    event_id=str(event.pk),
                    date=event.date,
                    type_label=type_label,
                    title=event.title,
                    status="Annulée" if event.status == Event.Status.CANCELLED else "Prévue",
                    availability=_availability_label(event, len(active)),
                    hidden=event.is_hidden,
                    minimum=event.min_organizers,
                    capacity=event.capacity,
                    active_count=len(active),
                    organizer_names=tuple(reg.organizer_name for reg in active),
                    public_note=event.public_note,
                )
            )
            for reg in event.report_regs:
                registration_rows.append(
                    RegistrationRow(
                        event_id=str(event.pk),
                        date=event.date,
                        type_label=type_label,
                        hidden=event.is_hidden,
                        organizer_name=reg.organizer_name,
                        state="Inscrit" if reg.state == Registration.State.ACTIVE else "Retiré",
                        note=reg.note,
                    )
                )
        return PeriodTable(
            period_id=str(period.pk),
            label=period.label,
            start_date=period.start_date,
            end_date=period.end_date,
            archived=period.is_archived,
            planning=tuple(planning_rows),
            registrations=tuple(registration_rows),
        )


def public_recap(period):
    """Public recap matches visible active registrations, not attendance."""
    with transaction.atomic():
        organizers = {
            organizer.pk: organizer
            for organizer in Organizer.objects.only("id", "name", "merged_into_id")
        }
        roots = {}

        def root_id(organizer_id):
            trail = set()
            current = organizer_id
            while current not in roots:
                if current in trail or current not in organizers:
                    raise ValueError("Historique de fusion des pseudos incohérent.")
                trail.add(current)
                target = organizers[current].merged_into_id
                if target is None:
                    break
                current = target
            root = roots.get(current, current)
            for item in trail:
                roots[item] = root
            return root

        registrations = (
            Registration.objects.filter(
                event__period_id=period.pk,
                event__is_hidden=False,
                state=Registration.State.ACTIVE,
            )
            .select_related("event")
            .order_by("event__date", "event_id", "created_at", "pk")
        )
        grouped = {}
        for registration in registrations:
            event = registration.event
            organizer_id = root_id(registration.organizer_id)
            dates = grouped.setdefault(organizer_id, {})
            # Archived aliases can retain two records for the same person/date.
            dates.setdefault(
                event.pk,
                RecapDate(
                    event_id=str(event.pk),
                    date=event.date,
                    type_label=event.type_label or "À définir",
                    cancelled=event.status == Event.Status.CANCELLED,
                    availability=event.get_availability_display(),
                ),
            )
        return tuple(
            RecapPerson(
                organizer_id=str(organizer_id),
                name=organizers[organizer_id].name,
                dates=tuple(dates.values()),
            )
            for organizer_id, dates in sorted(
                grouped.items(),
                key=lambda item: (organizers[item[0]].name.casefold(), str(item[0])),
            )
        )
