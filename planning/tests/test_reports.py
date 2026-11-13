import uuid
from datetime import date

from django.test import TestCase

from planning.models import Event, Organizer, Period, Registration
from planning.reports import period_table


class ReportDataTests(TestCase):
    def setUp(self):
        self.period = Period.objects.create(
            creation_key=uuid.uuid4(),
            label="Janvier 2030",
            start_date=date(2030, 1, 1),
            end_date=date(2030, 1, 31),
        )
        self.alice = self._organizer("Alice")
        self.bob = self._organizer("Bob")
        self.carol = self._organizer("Carol")
        self.hidden_person = self._organizer("Personne masquée")
        self.first = self._event(5)
        self.second = self._event(6, availability=Event.Availability.UNAVAILABLE)
        self.hidden = self._event(12, is_hidden=True)
        self.cancelled = self._event(13, status=Event.Status.CANCELLED)
        self._registration(self.first, self.alice)
        self._registration(self.first, self.bob)
        self._registration(self.first, self.carol, state=Registration.State.WITHDRAWN)
        self._registration(self.second, self.alice)
        self._registration(self.hidden, self.hidden_person)
        self._registration(self.cancelled, self.bob)

    def _organizer(self, name, **kwargs):
        return Organizer.objects.create(name=name, normalized_key=name.casefold(), **kwargs)

    def _event(self, day, **kwargs):
        return Event.objects.create(period=self.period, date=date(2030, 1, day), **kwargs)

    def _registration(self, event, organizer, **kwargs):
        return Registration.objects.create(
            event=event, organizer=organizer, organizer_name=organizer.name, **kwargs
        )

    def test_bureau_table_keeps_hidden_dates_and_withdrawn_history(self):
        report = period_table(self.period)
        self.assertEqual(
            [row.date for row in report.planning],
            [date(2030, 1, day) for day in (5, 6, 12, 13)],
        )
        self.assertEqual([row.active_count for row in report.planning], [2, 1, 1, 1])
        self.assertEqual(report.planning[0].organizer_names, ("Alice", "Bob"))
        self.assertEqual(report.planning[1].availability, "Indisponible")
        self.assertTrue(report.planning[2].hidden)
        self.assertEqual(report.planning[3].status, "Annulée")
        self.assertEqual(len(report.registrations), 6)
        withdrawn = [row for row in report.registrations if row.state == "Retiré"]
        self.assertEqual([row.organizer_name for row in withdrawn], ["Carol"])

    def test_capacity_full_is_not_confused_with_minimum_or_manual_state(self):
        self.first.capacity = 2
        self.first.save(update_fields=["capacity"])
        report = period_table(self.period)
        self.assertEqual(report.planning[0].availability, "Complète (capacité atteinte)")
        self.first.capacity = None
        self.first.save(update_fields=["capacity"])
        self.assertEqual(period_table(self.period).planning[0].availability, "Ouverte")

    def test_historical_snapshot_name_and_note_remain_in_export(self):
        registration = Registration.objects.get(event=self.first, organizer=self.alice)
        registration.organizer_name = "=Ancien nom"
        registration.note = "=1+1"
        registration.save(update_fields=["organizer_name", "note"])
        self.alice.name = "Nouveau nom"
        self.alice.save(update_fields=["name"])
        report = period_table(self.period)
        self.assertIn("=Ancien nom", report.planning[0].organizer_names)
        row = next(row for row in report.registrations if row.organizer_name == "=Ancien nom")
        self.assertEqual(row.note, "=1+1")
