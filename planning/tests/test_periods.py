import uuid
from datetime import date

from django.test import TestCase

from planning import selectors
from planning.errors import (
    ArchivedConflict,
    Conflict,
    ValidationFailed,
    VersionConflict,
)
from planning.models import Event, Period, SiteSettings
from planning.services import events as event_services
from planning.services import periods as period_services
from planning.services.actors import Actor

BUREAU = Actor.bureau()


def make_period(start=date(2027, 1, 1), end=date(2027, 6, 30), key=None, **kwargs):
    return period_services.create_period(
        actor=BUREAU,
        label=kwargs.pop("label", "S1 2027"),
        start_date=start,
        end_date=end,
        creation_key=key or uuid.uuid4(),
        **kwargs,
    )


class PeriodCreationTests(TestCase):
    def test_creates_52_weekend_events(self):
        period = make_period()
        self.assertEqual(period.events.count(), 52)
        event = period.events.get(date=date(2027, 1, 2))
        self.assertEqual(event.origin, Event.Origin.GENERATED)
        self.assertEqual(event.min_organizers, 2)
        self.assertIsNone(event.capacity)
        self.assertIsNone(event.game_type)
        self.assertTrue(event.registrations_open)

    def test_creation_key_resubmit_returns_existing(self):
        key = uuid.uuid4()
        first = make_period(key=key)
        second = make_period(key=key, label="Autre libellé")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Period.objects.count(), 1)
        self.assertEqual(first.events.count(), 52)

    def test_selected_dates_subset(self):
        period = make_period(selected_dates=[date(2027, 1, 2), date(2027, 1, 9)])
        self.assertEqual(period.events.count(), 2)

    def test_selected_dates_must_be_weekends_in_range(self):
        with self.assertRaises(ValidationFailed):
            make_period(selected_dates=[date(2027, 1, 4)])

    def test_overlap_requires_confirmation(self):
        make_period()
        with self.assertRaises(Conflict) as ctx:
            make_period(start=date(2027, 6, 1), end=date(2027, 12, 31), label="S2")
        self.assertEqual(ctx.exception.code, "overlap")
        second = make_period(
            start=date(2027, 6, 1),
            end=date(2027, 12, 31),
            label="S2",
            confirm_overlap=True,
        )
        self.assertEqual(Period.objects.count(), 2)
        self.assertTrue(second.events.filter(date=date(2027, 6, 5)).exists())

    def test_preview_reports_overlap(self):
        make_period()
        preview = period_services.preview_period(
            start_date=date(2027, 6, 1), end_date=date(2027, 6, 30)
        )
        self.assertTrue(preview["overlaps"])
        self.assertEqual(preview["overlaps"][0]["periods"], ["S1 2027"])


class CompleteMissingTests(TestCase):
    def test_regeneration_only_adds_missing(self):
        period = make_period()
        cancelled = period.events.get(date=date(2027, 1, 9))
        event_services.cancel_event(cancelled.pk, actor=BUREAU)
        closed = period.events.get(date=date(2027, 1, 16))
        event_services.set_registrations_open(closed.pk, actor=BUREAU, is_open=False)
        typed = period.events.get(date=date(2027, 1, 23))
        from planning.models import GameType

        game_type = GameType.objects.get(code="flex")
        event_services.update_event(typed.pk, actor=BUREAU, game_type_id=game_type.pk)
        period.events.get(date=date(2027, 1, 2)).delete()
        missing = period_services.complete_missing_weekends(period.pk, actor=BUREAU)
        self.assertEqual(missing, [date(2027, 1, 2)])
        self.assertEqual(period.events.count(), 52)
        cancelled.refresh_from_db()
        closed.refresh_from_db()
        typed.refresh_from_db()
        self.assertEqual(cancelled.status, Event.Status.CANCELLED)
        self.assertFalse(cancelled.registrations_open)
        self.assertFalse(closed.registrations_open)
        self.assertEqual(typed.game_type_id, game_type.pk)
        new_event = period.events.get(date=date(2027, 1, 2))
        self.assertTrue(new_event.registrations_open)

    def test_nothing_missing_creates_nothing(self):
        period = make_period()
        revision = period.revision
        missing = period_services.complete_missing_weekends(period.pk, actor=BUREAU)
        self.assertEqual(missing, [])
        period.refresh_from_db()
        self.assertEqual(period.revision, revision)


class PeriodBoundsTests(TestCase):
    def test_shrinking_bounds_excluding_events_rejected(self):
        period = make_period()
        with self.assertRaises(Conflict) as ctx:
            period_services.update_period(period.pk, actor=BUREAU, end_date=date(2027, 1, 15))
        self.assertIn("2027-01-16", str(ctx.exception.fields["excluded_dates"]))
        period.refresh_from_db()
        self.assertEqual(period.end_date, date(2027, 6, 30))
        self.assertEqual(period.events.count(), 52)

    def test_extending_bounds_keeps_events(self):
        period = make_period()
        period_services.update_period(period.pk, actor=BUREAU, end_date=date(2027, 7, 31))
        period.refresh_from_db()
        self.assertEqual(period.end_date, date(2027, 7, 31))
        self.assertEqual(period.events.count(), 52)

    def test_version_conflict(self):
        period = make_period()
        with self.assertRaises(VersionConflict):
            period_services.update_period(
                period.pk,
                actor=BUREAU,
                expected_version=period.version + 5,
                label="X",
            )


class ArchiveReopenTests(TestCase):
    def test_archive_blocks_mutation_and_clears_active(self):
        period = make_period()
        period_services.set_active(period.pk, actor=BUREAU)
        period_services.archive(period.pk, actor=BUREAU)
        self.assertIsNone(SiteSettings.get().active_period)
        with self.assertRaises(ArchivedConflict):
            period_services.update_period(period.pk, actor=BUREAU, label="X")
        with self.assertRaises(ArchivedConflict):
            period_services.complete_missing_weekends(period.pk, actor=BUREAU)
        event = period.events.first()
        with self.assertRaises(ArchivedConflict):
            event_services.cancel_event(event.pk, actor=BUREAU)
        period_services.reopen(period.pk, actor=BUREAU)
        period_services.update_period(period.pk, actor=BUREAU, label="Corrigé")
        period.refresh_from_db()
        self.assertEqual(period.label, "Corrigé")


class DefaultPeriodSelectorTests(TestCase):
    def test_requested_wins(self):
        p1 = make_period()
        p2 = make_period(start=date(2027, 7, 1), end=date(2027, 12, 31), label="S2")
        period_services.set_active(p1.pk, actor=BUREAU)
        self.assertEqual(selectors.default_period(p2.pk).pk, p2.pk)

    def test_active_fallback(self):
        p1 = make_period()
        make_period(start=date(2027, 7, 1), end=date(2027, 12, 31), label="S2")
        period_services.set_active(p1.pk, actor=BUREAU)
        self.assertEqual(selectors.default_period().pk, p1.pk)

    def test_next_event_period_when_no_active(self):
        p1 = make_period(start=date(2020, 1, 1), end=date(2020, 6, 30), label="old")
        p2 = make_period(start=date(2030, 1, 1), end=date(2030, 6, 30), label="new")
        self.assertEqual(selectors.default_period().pk, p2.pk)
        p2.events.all().delete()
        fallback = selectors.default_period()
        self.assertEqual(fallback.pk, p2.pk)
        p2.delete()
        self.assertEqual(selectors.default_period().pk, p1.pk)

    def test_empty_returns_none(self):
        self.assertIsNone(selectors.default_period())


class ManualEventTests(TestCase):
    def test_weekday_event_inside_bounds(self):
        period = make_period()
        event = event_services.create_event(period.pk, actor=BUREAU, date_value=date(2027, 1, 13))
        self.assertEqual(event.origin, Event.Origin.MANUAL)
        self.assertTrue(event.registrations_open)

    def test_event_outside_bounds_rejected(self):
        period = make_period()
        with self.assertRaises(ValidationFailed):
            event_services.create_event(period.pk, actor=BUREAU, date_value=date(2027, 7, 3))

    def test_duplicate_date_rejected(self):
        period = make_period()
        with self.assertRaises(Conflict):
            event_services.create_event(period.pk, actor=BUREAU, date_value=date(2027, 1, 2))

    def test_date_change_keeps_registrations(self):
        from planning.models import Registration
        from planning.services import organizers, registrations

        period = make_period()
        event = period.events.get(date=date(2027, 1, 2))
        organizer = organizers.get_or_create_organizer("Cha")
        registrations.set_registration(
            event.pk, organizer.pk, Registration.State.ACTIVE, actor=Actor.public(organizer)
        )
        event_services.update_event(event.pk, actor=BUREAU, date_value=date(2027, 1, 6))
        event.refresh_from_db()
        self.assertEqual(event.date, date(2027, 1, 6))
        self.assertEqual(event.registrations.count(), 1)
