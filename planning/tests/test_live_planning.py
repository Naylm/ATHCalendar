import uuid
from datetime import date, timedelta
from unittest import mock

from django.template.loader import render_to_string
from django.test import Client, TestCase

from planning.models import Event, GameType
from planning.services import organizers, periods, registrations
from planning.services.actors import Actor
from planning.tests import web_client

BUREAU = Actor.bureau()


@web_client
class PeriodLiveApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        today = date.today()
        self.period = periods.create_period(
            actor=BUREAU,
            label="S1 test",
            start_date=today + timedelta(days=1),
            end_date=today + timedelta(days=120),
            creation_key=uuid.uuid4(),
        )
        self.event = self.period.events.order_by("date").first()
        self.url = f"/api/periods/{self.period.pk}/live/"

    def _revision(self):
        self.period.refresh_from_db()
        return self.period.revision

    def test_same_revision_returns_unchanged(self):
        response = self.client.get(self.url, {"revision": str(self._revision())})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["changed"])
        self.assertNotIn("html", data)
        self.assertEqual(data["revision"], self._revision())

    def test_missing_revision_forces_snapshot(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["changed"])
        self.assertIn("html", data)

    def test_lower_revision_returns_html(self):
        response = self.client.get(self.url, {"revision": "0"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["changed"])
        self.assertIn("html", data)
        self.assertEqual(data["revision"], self._revision())

    def test_snapshot_contains_names_types_and_month_key(self):
        organizer = organizers.get_or_create_organizer("Al<i>")
        registrations.set_registration(
            self.event.pk,
            organizer.pk,
            "active",
            actor=Actor.public(organizer),
        )
        response = self.client.get(self.url)
        data = response.json()
        self.assertTrue(data["changed"])
        html = data["html"]
        self.assertIn("Al&lt;i&gt;", html)
        self.assertNotIn("Al<i>", html)
        self.assertIn(f'data-month="{self.event.date:%Y-%m}"', html)
        flex = GameType.objects.get(name="Flex")
        self.assertIn(str(flex.pk), html)

    def test_revision_captured_before_render(self):
        revision = self._revision()
        original = render_to_string

        def mutate_during_render(*args, **kwargs):
            event = Event.objects.get(pk=self.event.pk)
            event.version += 1
            event.save(update_fields=["version"])
            self.period.revision += 1
            self.period.save(update_fields=["revision"])
            return original(*args, **kwargs)

        with mock.patch("planning.api_public.render_to_string", side_effect=mutate_during_render):
            response = self.client.get(self.url, {"revision": str(revision - 1)})
        data = response.json()
        self.assertTrue(data["changed"])
        self.assertEqual(data["revision"], revision)

        next_response = self.client.get(self.url, {"revision": str(data["revision"])})
        next_data = next_response.json()
        self.assertTrue(next_data["changed"])
        self.assertEqual(next_data["revision"], self._revision())

    def test_invalid_revision_400(self):
        for value in ("abc", "-1", "1.5", ""):
            response = self.client.get(self.url, {"revision": value})
            self.assertEqual(response.status_code, 400, value)
            self.assertIn("no-store", response["Cache-Control"])

    def test_malformed_revision_400(self):
        for value in ("²", "9" * 5000, "٣", "1e3", " 1", "1 "):
            response = self.client.get(self.url, {"revision": value})
            self.assertEqual(response.status_code, 400, value)

    def test_unknown_period_404(self):
        response = self.client.get(f"/api/periods/{uuid.uuid4()}/live/")
        self.assertEqual(response.status_code, 404)
        self.assertIn("no-store", response["Cache-Control"])

    def test_post_405(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 405)
        self.assertIn("no-store", response["Cache-Control"])

    def test_no_store_header(self):
        for params in ({}, {"revision": str(self._revision())}):
            response = self.client.get(self.url, params)
            self.assertIn("no-store", response["Cache-Control"])

    def test_filtre_membership(self):
        past_event = self.period.events.order_by("-date").first()
        past_event.date = date.today() - timedelta(days=1)
        past_event.save(update_fields=["date"])
        response = self.client.get(self.url, {"filtre": "avenir"})
        html = response.json()["html"]
        self.assertNotIn(f'id="date-{past_event.pk}"', html)
        response = self.client.get(self.url, {"filtre": "toutes"})
        html = response.json()["html"]
        self.assertIn(f'id="date-{past_event.pk}"', html)
