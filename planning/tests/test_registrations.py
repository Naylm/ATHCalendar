import uuid
from datetime import date

from django.test import TestCase

from planning.errors import (
    ArchivedConflict,
    CapacityConflict,
    ClosedConflict,
    Conflict,
    ValidationFailed,
    VersionConflict,
)
from planning.models import Event, Registration
from planning.services import events as event_services
from planning.services import organizers, registrations
from planning.services import periods as period_services
from planning.services.actors import Actor

BUREAU = Actor.bureau()


class RegistrationTests(TestCase):
    def setUp(self):
        self.period = period_services.create_period(
            actor=BUREAU,
            label="S1 2027",
            start_date=date(2027, 1, 1),
            end_date=date(2027, 6, 30),
            creation_key=uuid.uuid4(),
        )
        self.event = self.period.events.get(date=date(2027, 1, 2))
        self.organizer = organizers.get_or_create_organizer("Cha")
        self.public = Actor.public(self.organizer)

    def register(self, **kwargs):
        return registrations.set_registration(
            self.event.pk, self.organizer.pk, "active", actor=self.public, **kwargs
        )

    def test_register_withdraw_reactivate(self):
        reg = self.register()
        self.assertEqual(reg.state, "active")
        self.assertEqual(reg.version, 1)
        reg = registrations.set_registration(
            self.event.pk, self.organizer.pk, "withdrawn", actor=self.public
        )
        self.assertEqual(reg.state, "withdrawn")
        self.assertEqual(reg.version, 2)
        reg = self.register()
        self.assertEqual(reg.state, "active")
        self.assertEqual(reg.version, 3)
        self.assertEqual(Registration.objects.count(), 1)

    def test_double_click_idempotent(self):
        reg1 = self.register(note="ok")
        reg2 = self.register(note="ok")
        self.assertEqual(reg1.pk, reg2.pk)
        self.assertEqual(reg2.version, 1)
        self.assertEqual(Registration.objects.count(), 1)

    def test_note_update_and_stale_version_409(self):
        reg = self.register(note="a")
        updated = registrations.set_registration(
            self.event.pk,
            self.organizer.pk,
            "active",
            actor=self.public,
            note="b",
            expected_version=reg.version,
        )
        self.assertEqual(updated.note, "b")
        self.assertEqual(updated.version, 2)
        with self.assertRaises(VersionConflict):
            registrations.set_registration(
                self.event.pk,
                self.organizer.pk,
                "active",
                actor=self.public,
                note="c",
                expected_version=1,
            )

    def test_other_organizer_join_does_not_bump_event_version(self):
        other = organizers.get_or_create_organizer("Alex")
        version_before = self.event.version
        revision_before = self.period.revision
        self.register()
        registrations.set_registration(self.event.pk, other.pk, "active", actor=Actor.public(other))
        self.event.refresh_from_db()
        self.period.refresh_from_db()
        self.assertEqual(self.event.version, version_before)
        self.assertGreater(self.period.revision, revision_before)

    def test_capacity_optional_and_enforced(self):
        event_services.update_event(self.event.pk, actor=BUREAU, capacity=2)
        other = organizers.get_or_create_organizer("Alex")
        self.register()
        registrations.set_registration(self.event.pk, other.pk, "active", actor=Actor.public(other))
        third = organizers.get_or_create_organizer("Bob")
        with self.assertRaises(CapacityConflict):
            registrations.set_registration(
                self.event.pk, third.pk, "active", actor=Actor.public(third)
            )
        self.assertEqual(self.event.registrations.filter(state="active").count(), 2)

    def test_min_is_not_max(self):
        event_services.update_event(self.event.pk, actor=BUREAU, min_organizers=2)
        for name in ["A1", "A2", "A3"]:
            o = organizers.get_or_create_organizer(name)
            registrations.set_registration(self.event.pk, o.pk, "active", actor=Actor.public(o))
        self.assertEqual(self.event.registrations.filter(state="active").count(), 3)

    def test_capacity_below_registered_rejected_no_eviction(self):
        self.register()
        for name in ["Alex", "Bob"]:
            o = organizers.get_or_create_organizer(name)
            registrations.set_registration(self.event.pk, o.pk, "active", actor=Actor.public(o))
        with self.assertRaises(Conflict):
            event_services.update_event(self.event.pk, actor=BUREAU, capacity=2)
        self.assertEqual(self.event.registrations.filter(state="active").count(), 3)

    def test_closed_flag_does_not_block_public(self):
        event_services.set_registrations_open(self.event.pk, actor=BUREAU, is_open=False)
        reg = self.register()
        self.assertEqual(reg.state, "active")
        self.assertFalse(Event.objects.get(pk=self.event.pk).registrations_open)

    def test_cancelled_event_blocks_public(self):
        event_services.cancel_event(self.event.pk, actor=BUREAU)
        with self.assertRaises(ClosedConflict):
            self.register()

    def test_past_event_blocks_public(self):
        past_period = period_services.create_period(
            actor=BUREAU,
            label="old",
            start_date=date(2020, 1, 1),
            end_date=date(2020, 6, 30),
            creation_key=uuid.uuid4(),
        )
        past_event = past_period.events.first()
        with self.assertRaises(ClosedConflict):
            registrations.set_registration(
                past_event.pk, self.organizer.pk, "active", actor=self.public
            )

    def test_bureau_can_correct_closed_past_cancelled_not_archived(self):
        event_services.cancel_event(self.event.pk, actor=BUREAU)
        reg = registrations.set_registration(
            self.event.pk, self.organizer.pk, "active", actor=BUREAU
        )
        self.assertEqual(reg.state, "active")
        period_services.archive(self.period.pk, actor=BUREAU)
        with self.assertRaises(ArchivedConflict):
            registrations.set_registration(
                self.event.pk, self.organizer.pk, "withdrawn", actor=BUREAU
            )
        with self.assertRaises(ArchivedConflict):
            registrations.set_registration(
                self.event.pk, self.organizer.pk, "withdrawn", actor=self.public
            )

    def test_restore_leaves_flag_but_allows_signup(self):
        event_services.cancel_event(self.event.pk, actor=BUREAU)
        event_services.restore_event(self.event.pk, actor=BUREAU)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.SCHEDULED)
        self.assertFalse(self.event.registrations_open)
        reg = self.register()
        self.assertEqual(reg.state, "active")
        self.assertFalse(Event.objects.get(pk=self.event.pk).registrations_open)

    def test_inactive_pseudo_blocked(self):
        organizers.set_organizer_active(self.organizer.pk, actor=BUREAU, active=False)
        with self.assertRaises(ClosedConflict):
            self.register()

    def test_batch_atomic_rollback(self):
        open_event = self.period.events.get(date=date(2027, 1, 9))
        locked_event = self.period.events.get(date=date(2027, 1, 16))
        event_services.set_event_hidden(
            locked_event.pk,
            actor=BUREAU,
            is_hidden=True,
            expected_version=locked_event.version,
        )
        with self.assertRaises(Conflict):
            registrations.batch_set(
                [open_event.pk, locked_event.pk],
                self.organizer.pk,
                "active",
                actor=self.public,
            )
        self.assertEqual(Registration.objects.count(), 0)

    def test_batch_dedupes_and_limits(self):
        ids = [self.event.pk] * 3
        regs = registrations.batch_set(ids, self.organizer.pk, "active", actor=self.public)
        self.assertEqual(len(regs), 1)
        with self.assertRaises(ValidationFailed):
            registrations.batch_set(
                [uuid.uuid4() for _ in range(201)],
                self.organizer.pk,
                "active",
                actor=self.public,
            )

    def test_note_max_length(self):
        with self.assertRaises(ValidationFailed):
            self.register(note="x" * 501)
