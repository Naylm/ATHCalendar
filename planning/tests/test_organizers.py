import uuid
from datetime import date

from django.test import TestCase

from planning.errors import Conflict, ValidationFailed
from planning.models import Registration
from planning.services import organizers, periods, registrations
from planning.services.actors import Actor

BUREAU = Actor.bureau()


def setup_period(**kwargs):
    return periods.create_period(
        actor=BUREAU,
        label="S1 2027",
        start_date=kwargs.get("start", date(2027, 1, 1)),
        end_date=kwargs.get("end", date(2027, 6, 30)),
        creation_key=uuid.uuid4(),
    )


class NormalizationTests(TestCase):
    def test_nfkc_space_casefold(self):
        self.assertEqual(organizers.normalize_pseudo("  Ch\u00e2  MARIE "), "ch\u00e2 marie")
        self.assertEqual(organizers.normalize_pseudo("\ufb01ne"), "fine")

    def test_equivalent_creation_returns_existing(self):
        first = organizers.get_or_create_organizer("Cha")
        second = organizers.get_or_create_organizer("  CHA  ")
        self.assertEqual(first.pk, second.pk)

    def test_length_limits(self):
        with self.assertRaises(ValidationFailed):
            organizers.get_or_create_organizer("x")
        with self.assertRaises(ValidationFailed):
            organizers.get_or_create_organizer("x" * 41)


class MergeTests(TestCase):
    def setUp(self):
        self.period = setup_period()
        self.event1 = self.period.events.get(date=date(2027, 1, 2))
        self.event2 = self.period.events.get(date=date(2027, 1, 3))
        self.source = organizers.get_or_create_organizer("Cha")
        self.target = organizers.get_or_create_organizer("Charlie")

    def test_reassigns_non_conflicting(self):
        registrations.set_registration(
            self.event1.pk, self.source.pk, "active", actor=Actor.public(self.source)
        )
        organizers.merge_organizers(self.source.pk, self.target.pk, actor=BUREAU)
        self.source.refresh_from_db()
        self.assertFalse(self.source.is_active)
        self.assertEqual(self.source.merged_into_id, self.target.pk)
        self.assertTrue(
            Registration.objects.filter(
                event=self.event1, organizer=self.target, state="active"
            ).exists()
        )
        self.assertEqual(organizers.get_or_create_organizer("Cha").pk, self.target.pk)

    def test_conflict_requires_explicit_note_choice(self):
        registrations.set_registration(
            self.event1.pk,
            self.source.pk,
            "active",
            actor=Actor.public(self.source),
            note="source note",
        )
        registrations.set_registration(
            self.event1.pk,
            self.target.pk,
            "active",
            actor=Actor.public(self.target),
            note="target note",
        )
        with self.assertRaises(Conflict) as ctx:
            organizers.merge_organizers(self.source.pk, self.target.pk, actor=BUREAU)
        self.assertEqual(ctx.exception.code, "merge_note_conflict")
        organizers.merge_organizers(
            self.source.pk,
            self.target.pk,
            actor=BUREAU,
            note_choices={str(self.event1.pk): "chosen final"},
        )
        reg = Registration.objects.get(event=self.event1, organizer=self.target)
        self.assertEqual(reg.note, "chosen final")
        self.assertEqual(reg.state, "active")
        src_reg = Registration.objects.get(event=self.event1, organizer=self.source)
        self.assertEqual(src_reg.state, "withdrawn")

    def test_same_note_conflict_free(self):
        registrations.set_registration(
            self.event1.pk,
            self.source.pk,
            "active",
            actor=Actor.public(self.source),
            note="same",
        )
        registrations.set_registration(
            self.event1.pk,
            self.target.pk,
            "active",
            actor=Actor.public(self.target),
            note="same",
        )
        organizers.merge_organizers(self.source.pk, self.target.pk, actor=BUREAU)
        self.assertEqual(
            Registration.objects.get(event=self.event1, organizer=self.target).note,
            "same",
        )

    def test_archived_registrations_untouched(self):
        archived_period = setup_period(start=date(2020, 1, 1), end=date(2020, 6, 30))
        old_event = archived_period.events.first()
        registrations.set_registration(
            old_event.pk,
            self.source.pk,
            "active",
            actor=BUREAU,
            note="archive note",
        )
        periods.archive(archived_period.pk, actor=BUREAU)
        organizers.merge_organizers(self.source.pk, self.target.pk, actor=BUREAU)
        reg = Registration.objects.get(event=old_event, organizer=self.source)
        self.assertEqual(reg.state, "active")
        self.assertEqual(reg.note, "archive note")

    def test_no_self_merge(self):
        with self.assertRaises(ValidationFailed):
            organizers.merge_organizers(self.source.pk, self.source.pk, actor=BUREAU)

    def test_rename_conflict(self):
        with self.assertRaises(Conflict):
            organizers.rename_organizer(self.target.pk, actor=BUREAU, name="  cha ")

    def test_rename_ok(self):
        organizers.rename_organizer(self.target.pk, actor=BUREAU, name="Charles")
        self.target.refresh_from_db()
        self.assertEqual(self.target.name, "Charles")
