import uuid
from datetime import date

from django.test import Client, TestCase

from planning.services import periods
from planning.services.actors import Actor
from planning.tests import web_client


@web_client
class ViewSmokeTests(TestCase):
    def test_healthz(self):
        response = Client().get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_index_empty(self):
        response = Client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "pas encore disponible")

    def test_index_lists_events(self):
        period = periods.create_period(
            actor=Actor.bureau(),
            label="S1 2027",
            start_date=date(2027, 1, 1),
            end_date=date(2027, 6, 30),
            creation_key=uuid.uuid4(),
        )
        periods.set_active(period.pk, actor=Actor.bureau())
        response = Client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "S1 2027")
        self.assertContains(response, "minimum 2")

    def test_noindex_header(self):
        response = Client().get("/")
        self.assertIn("noindex", response.headers["X-Robots-Tag"])
