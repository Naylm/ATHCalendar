import uuid
from datetime import date

from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase

from planning.db import write_transaction
from planning.errors import (
    ClosedConflict,
    Conflict,
    NotFound,
    ValidationFailed,
    VersionConflict,
)
from planning.models import Event, Organizer, Period, Registration
from planning.services import events as event_services
from planning.services import organizers
from planning.services import periods as period_services
from planning.services import registrations
from planning.services.actors import Actor

BUREAU = Actor.bureau()


def make_period(start=date(2027, 1, 1), end=date(2027, 6, 30), label="S1"):
    return period_services.create_period(
        actor=BUREAU,
        label=label,
        start_date=start,
        end_date=end,
        creation_key=uuid.uuid4(),
    )


class WriteTransactionTests(TransactionTestCase):
    def test_on_commit_runs_once_on_nested(self):
        calls = []

        def cb():
            calls.append(1)

        with write_transaction():
            with write_transaction():
                transaction.on_commit(cb)
        self.assertEqual(calls, [1])

    def test_rollback_no_partial_writes(self):
        period = make_period()
        with self.assertRaises(ValidationFailed):
            with write_transaction():
                Event.objects.create(
                    period=period, date=date(2027, 1, 6), min_organizers=2
                )
                raise ValidationFailed("stop")
        self.assertFalse(
            Event.objects.filter(date=date(2027, 1, 6)).exists()
        )
        self.assertTrue(connection.get_autocommit())

    def test_non_lock_errors_not_masked(self):
        with self.assertRaises(ValidationFailed):
            with write_transaction():
                raise ValidationFailed("typed")


class EventValidationTests(TestCase):
    def setUp(self):
        self.period = make_period()
        self.event = self.period.events.get(date=date(2027, 1, 2))

    def test_capacity_below_min_typed_400(self):
        with self.assertRaises(ValidationFailed):
            event_services.create_event(
                self.period.pk,
                actor=BUREAU,
                date_value=date(2027, 1, 6),
                capacity=1,
            )

    def test_no_silent_truncation(self):
        with self.assertRaises(ValidationFailed):
            event_services.update_event(
                self.event.pk, actor=BUREAU, title="x" * 121
            )
        with self.assertRaises(ValidationFailed):
            event_services.create_event(
                self.period.pk,
                actor=BUREAU,
                date_value=date(2027, 1, 7),
                public_note="y" * 1001,
            )

    def test_open_cancelled_rejected(self):
        event_services.cancel_event(self.event.pk, actor=BUREAU)
        with self.assertRaises(ClosedConflict):
            event_services.set_registrations_open(
                self.event.pk, actor=BUREAU, is_open=True
            )

    def test_restore_forces_closed(self):
        event_services.cancel_event(self.event.pk, actor=BUREAU)
        event_services.restore_event(self.event.pk, actor=BUREAU)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.SCHEDULED)
        self.assertFalse(self.event.registrations_open)

    def test_period_wide_close_bumps_event_version(self):
        v = self.event.version
        period_services.set_period_registrations(
            self.period.pk, actor=BUREAU, is_open=False
        )
        self.event.refresh_from_db()
        self.assertGreater(self.event.version, v)
        self.assertFalse(self.event.registrations_open)

    def test_archive_reopen_bump_period_version(self):
        period = make_period(date(2030, 1, 1), date(2030, 6, 30), "P2")
        v = period.version
        period_services.archive(period.pk, actor=BUREAU)
        period.refresh_from_db()
        self.assertGreater(period.version, v)
        v = period.version
        period_services.reopen(period.pk, actor=BUREAU)
        period.refresh_from_db()
        self.assertGreater(period.version, v)

    def test_batch_counts_and_rollback(self):
        o = organizers.get_or_create_organizer("B1")
        o2 = organizers.get_or_create_organizer("B2")
        other = self.period.events.get(date=date(2027, 1, 3))
        for org in (o, o2):
            registrations.set_registration(
                other.pk, org.pk, "active", actor=Actor.public(org)
            )
        result = event_services.batch_update(
            [self.event.pk, other.pk], actor=BUREAU, min_organizers=3
        )
        self.assertEqual(len(result["updated"]), 2)
        with self.assertRaises(Conflict):
            event_services.batch_update(
                [self.event.pk, other.pk], actor=BUREAU, capacity=1,
                min_organizers=1,
            )
        self.event.refresh_from_db()
        other.refresh_from_db()
        self.assertIsNone(self.event.capacity)
        self.assertIsNone(other.capacity)

    def test_batch_skips_cancelled_with_reason(self):
        event_services.cancel_event(self.event.pk, actor=BUREAU)
        other = self.period.events.get(date=date(2027, 1, 3))
        result = event_services.batch_update(
            [self.event.pk, other.pk], actor=BUREAU, registrations_open=True
        )
        self.assertEqual(result["skipped"][0]["event_id"], str(self.event.pk))
        other.refresh_from_db()
        self.assertTrue(other.registrations_open)


class EventVersionContractTests(TestCase):
    def setUp(self):
        self.period = make_period()
        self.event = self.period.events.get(date=date(2027, 1, 2))
        self.organizer = organizers.get_or_create_organizer("C1")

    def test_stale_event_version_409(self):
        displayed_version = self.event.version
        event_services.update_event(
            self.event.pk, actor=BUREAU, date_value=date(2027, 1, 6)
        )
        with self.assertRaises(VersionConflict):
            registrations.set_registration(
                self.event.pk,
                self.organizer.pk,
                "active",
                actor=Actor.public(self.organizer),
                expected_event_version=displayed_version,
            )

    def test_batch_stale_version_full_rollback(self):
        other = self.period.events.get(date=date(2027, 1, 3))
        versions = {
            str(self.event.pk): {"event": self.event.version},
            str(other.pk): {"event": other.version + 9},
        }
        with self.assertRaises(VersionConflict):
            registrations.batch_set(
                [self.event.pk, other.pk],
                self.organizer.pk,
                "active",
                actor=Actor.public(self.organizer),
                versions=versions,
            )
        self.assertEqual(Registration.objects.count(), 0)


class MergeCorrectionTests(TestCase):
    def setUp(self):
        self.period = make_period()
        self.event = self.period.events.get(date=date(2027, 1, 2))
        self.a = organizers.get_or_create_organizer("AA")
        self.b = organizers.get_or_create_organizer("BB")
        self.c = organizers.get_or_create_organizer("CC")

    def test_note_choice_length_validated(self):
        registrations.set_registration(
            self.event.pk, self.a.pk, "active",
            actor=Actor.public(self.a), note="n1",
        )
        registrations.set_registration(
            self.event.pk, self.b.pk, "active",
            actor=Actor.public(self.b), note="n2",
        )
        with self.assertRaises(ValidationFailed):
            organizers.merge_organizers(
                self.a.pk, self.b.pk,
                actor=BUREAU,
                note_choices={str(self.event.pk): "x" * 501},
            )

    def test_alias_chain_flattened(self):
        organizers.merge_organizers(self.b.pk, self.c.pk, actor=BUREAU)
        organizers.merge_organizers(self.a.pk, self.c.pk, actor=BUREAU)
        self.b.refresh_from_db()
        self.assertEqual(self.b.merged_into_id, self.c.pk)
        ids = organizers.alias_ids(self.c)
        self.assertEqual(ids, {self.a.pk, self.b.pk, self.c.pk})

    def test_merged_reactivation_rejected(self):
        organizers.merge_organizers(self.a.pk, self.b.pk, actor=BUREAU)
        with self.assertRaises(ValidationFailed):
            organizers.set_organizer_active(
                self.a.pk, actor=BUREAU, active=True
            )

    def test_remerge_alias_rejected(self):
        organizers.merge_organizers(self.a.pk, self.b.pk, actor=BUREAU)
        with self.assertRaises(ValidationFailed):
            organizers.merge_organizers(self.a.pk, self.c.pk, actor=BUREAU)

    def test_rename_bumps_period_revision(self):
        registrations.set_registration(
            self.event.pk, self.a.pk, "active", actor=Actor.public(self.a)
        )
        self.period.refresh_from_db()
        before = self.period.revision
        organizers.rename_organizer(self.a.pk, actor=BUREAU, name="AA2")
        self.period.refresh_from_db()
        self.assertGreater(self.period.revision, before)
        reg = Registration.objects.get(event=self.event, organizer=self.a)
        self.assertEqual(reg.organizer_name, "AA2")


class LookupRobustnessTests(TestCase):
    def test_malformed_ids(self):
        with self.assertRaises(ValidationFailed):
            period_services.get_period("not-a-uuid")
        with self.assertRaises(ValidationFailed):
            event_services.get_event("zzz")
        with self.assertRaises(ValidationFailed):
            organizers.get_organizer(None)
        with self.assertRaises(NotFound):
            period_services.get_period(uuid.uuid4())
