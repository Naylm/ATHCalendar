import re
import uuid
from datetime import date

from django.test import Client, TestCase

from planning.models import Event
from planning.selectors import planning_context
from planning.services import events as event_services
from planning.services import periods as period_services
from planning.services.actors import Actor
from planning.tests import web_client

BUREAU = Actor.bureau()


def _row_pattern(event, marker):
    cls = "event-card weekend-start" if marker else "event-card(?! weekend-start)"
    return re.compile(rf'class="{cls}[^"]*"\s+id="date-{event.pk}"')


@web_client
class WeekendSeparatorTests(TestCase):
    def setUp(self):
        self.period = period_services.create_period(
            actor=BUREAU,
            label="Hiver 2027",
            start_date=date(2027, 1, 1),
            end_date=date(2027, 2, 28),
            creation_key=uuid.uuid4(),
        )
        self.by_date = {e.date: e for e in Event.objects.order_by("date")}

    def _marks(self, filtre=None):
        context = planning_context(self.period, filtre)
        return {
            event.date.isoformat(): event.weekend_start
            for month in context["months"]
            for event in month["events"]
        }

    def test_sat_sun_pairs_and_weekend_gaps(self):
        marks = self._marks()
        self.assertFalse(marks["2027-01-02"])
        self.assertFalse(marks["2027-01-03"])
        self.assertTrue(marks["2027-01-09"])
        self.assertFalse(marks["2027-01-10"])
        self.assertTrue(marks["2027-01-16"])
        self.assertFalse(marks["2027-01-17"])
        self.assertTrue(marks["2027-01-30"])
        self.assertFalse(marks["2027-01-31"])

    def test_hidden_saturday_recomputes_sunday(self):
        sat = self.by_date[date(2027, 1, 9)]
        sat.is_hidden = True
        sat.save(update_fields=["is_hidden"])
        marks = self._marks()
        self.assertNotIn("2027-01-09", marks)
        self.assertTrue(marks["2027-01-10"])

    def test_renfort_filter_recomputes_visible_list(self):
        event_services.cancel_event(self.by_date[date(2027, 1, 9)].pk, actor=BUREAU)
        marks = self._marks("renfort")
        self.assertNotIn("2027-01-09", marks)
        self.assertTrue(marks["2027-01-10"])

    def test_month_boundary_resets_markers(self):
        marks = self._marks()
        self.assertFalse(marks["2027-02-06"])
        self.assertFalse(marks["2027-02-07"])
        event_services.create_event(self.period.pk, actor=BUREAU, date_value=date(2027, 2, 1))
        marks = self._marks()
        self.assertFalse(marks["2027-02-01"])
        self.assertTrue(marks["2027-02-06"])

    def test_manual_weekday_events_standalone(self):
        event_services.create_event(self.period.pk, actor=BUREAU, date_value=date(2027, 1, 6))
        event_services.create_event(self.period.pk, actor=BUREAU, date_value=date(2027, 1, 7))
        marks = self._marks()
        self.assertTrue(marks["2027-01-06"])
        self.assertTrue(marks["2027-01-07"])
        self.assertTrue(marks["2027-01-09"])

    def test_index_and_live_html_carry_markers(self):
        period_services.set_active(self.period.pk, actor=BUREAU)
        sat = self.by_date[date(2027, 1, 9)]
        sun = self.by_date[date(2027, 1, 10)]
        first = self.by_date[date(2027, 1, 2)]
        client = Client()
        htmls = [
            client.get("/").content.decode(),
            client.get(f"/api/periods/{self.period.pk}/live/").json()["html"],
        ]
        visible = Event.objects.exclude(is_hidden=True).count()
        for html in htmls:
            self.assertRegex(html, _row_pattern(sat, True))
            self.assertRegex(html, _row_pattern(sun, False))
            self.assertRegex(html, _row_pattern(first, False))
            self.assertEqual(html.count("<li "), visible)
            self.assertEqual(html.count("weekend-start"), 7)
