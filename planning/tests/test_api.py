import json
import uuid
from datetime import date

from django.test import Client, TestCase

from planning.models import Registration
from planning.services import organizers, periods
from planning.services.actors import Actor
from planning.tests import web_client

BUREAU = Actor.bureau()


@web_client
class PublicApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.period = periods.create_period(
            actor=BUREAU,
            label="S1 2027",
            start_date=date(2027, 1, 1),
            end_date=date(2027, 6, 30),
            creation_key=uuid.uuid4(),
        )
        self.event = self.period.events.get(date=date(2027, 1, 2))
        self.organizer = organizers.get_or_create_organizer("ApiUser")

    def test_periods_list(self):
        data = self.client.get("/api/periods/").json()
        self.assertEqual(len(data["periods"]), 1)
        self.assertEqual(data["periods"][0]["label"], "S1 2027")
        self.assertEqual(data["default_period_id"], str(self.period.pk))

    def test_planning_payload(self):
        data = self.client.get(f"/api/periods/{self.period.pk}/planning/").json()
        self.assertEqual(len(data["events"]), 52)
        first = data["events"][0]
        self.assertEqual(first["type_label"], "À définir")
        self.assertEqual(first["min_organizers"], 2)
        self.assertEqual(first["fill"], "none")

    def test_revision(self):
        data = self.client.get(f"/api/periods/{self.period.pk}/revision/").json()
        self.assertEqual(data["revision"], self.period.revision)

    def test_organizer_create_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(
            "/api/organizers/create/",
            data=json.dumps({"name": "Nouveau Pseudo"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        response = client.post(
            "/api/organizers/create/",
            data=json.dumps({"name": "Nouveau Pseudo"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN="invalid-token",
        )
        self.assertEqual(response.status_code, 403)

    def test_organizer_create_with_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.get("/")
        csrf = client.cookies.get("csrftoken")
        self.assertIsNotNone(csrf)
        response = client.post(
            "/api/organizers/create/",
            data=json.dumps({"name": "Nouveau Pseudo"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf.value,
        )
        self.assertEqual(response.status_code, 201)
        organizers = self.client.get("/api/organizers/").json()["organizers"]
        names = [o["name"] for o in organizers]
        self.assertIn("Nouveau Pseudo", names)

    def test_registration_requires_event_version(self):
        response = self.client.put(
            f"/api/events/{self.event.pk}/registration/",
            data=json.dumps({"organizer_id": str(self.organizer.pk), "state": "active"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid")

    def test_registration_happy_path(self):
        response = self.client.put(
            f"/api/events/{self.event.pk}/registration/",
            data=json.dumps(
                {
                    "organizer_id": str(self.organizer.pk),
                    "state": "active",
                    "expected_event_version": self.event.version,
                    "expected_version": 0,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Registration.objects.count(), 1)
        regs = self.client.get(f"/api/organizers/{self.organizer.pk}/registrations/").json()[
            "registrations"
        ]
        self.assertEqual(len(regs), 1)
        self.assertEqual(regs[0]["state"], "active")

    def test_malformed_uuid_404(self):
        response = self.client.get("/api/periods/not-a-uuid/revision/")
        self.assertEqual(response.status_code, 404)
        response = self.client.put(
            "/api/events/not-a-uuid/registration/",
            data=json.dumps({"organizer_id": "x", "state": "active"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_absent_uuid_typed_404(self):
        response = self.client.get(f"/api/periods/{uuid.uuid4()}/revision/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "not_found")

    def test_method_not_allowed(self):
        response = self.client.delete(f"/api/events/{self.event.pk}/registration/")
        self.assertEqual(response.status_code, 405)
        response = self.client.post("/api/periods/")
        self.assertEqual(response.status_code, 405)
