import json
import uuid
from datetime import date, timedelta

from django.test import Client, TestCase

from planning.models import Event, Registration
from planning.services import events as event_services
from planning.services import organizers, registrations
from planning.services import periods as period_services
from planning.services.actors import Actor
from planning.tests import web_client

BUREAU = Actor.bureau()


@web_client
class PublicWithdrawalTests(TestCase):
    def setUp(self):
        self.client = Client()
        today = date.today()
        self.period = period_services.create_period(
            actor=BUREAU,
            label="S1 test",
            start_date=today + timedelta(days=1),
            end_date=today + timedelta(days=60),
            creation_key=uuid.uuid4(),
        )
        self.event = Event.objects.order_by("date").first()
        self.alice = organizers.get_or_create_organizer("Alice")
        self.url = f"/api/events/{self.event.pk}/registration/"

    def _reg(self):
        return registrations.set_registration(
            self.event.pk, self.alice.pk, "active", actor=Actor.public(self.alice)
        )

    def _payload(self, organizer, state, event_version, reg_version):
        return {
            "organizer_id": str(organizer.pk),
            "state": state,
            "expected_event_version": event_version,
            "expected_version": reg_version,
        }

    def _put(self, body):
        return self.client.put(
            self.url,
            json.dumps(body),
            content_type="application/json",
        )

    def _revision(self):
        self.period.refresh_from_db()
        return self.period.revision

    def test_anonymous_withdraw_active(self):
        reg = self._reg()
        event_version = self.event.version
        revision = self._revision()
        response = self._put(self._payload(self.alice, "withdrawn", event_version, reg.version))
        self.assertEqual(response.status_code, 200)
        reg.refresh_from_db()
        self.assertEqual(reg.state, "withdrawn")
        self.assertEqual(reg.version, 2)
        self.assertEqual(self._revision(), revision + 1)

    def test_stale_registration_version_409(self):
        reg = self._reg()
        response = self._put(
            self._payload(self.alice, "withdrawn", self.event.version, reg.version + 5)
        )
        self.assertEqual(response.status_code, 409)
        reg.refresh_from_db()
        self.assertEqual(reg.state, "active")

    def test_stale_event_version_409(self):
        reg = self._reg()
        response = self._put(
            self._payload(self.alice, "withdrawn", self.event.version + 5, reg.version)
        )
        self.assertEqual(response.status_code, 409)
        reg.refresh_from_db()
        self.assertEqual(reg.state, "active")

    def test_closed_event_allows_withdrawal(self):
        reg = self._reg()
        event_services.set_registrations_open(self.event.pk, actor=BUREAU, is_open=False)
        self.event.refresh_from_db()
        response = self._put(
            self._payload(self.alice, "withdrawn", self.event.version, reg.version)
        )
        self.assertEqual(response.status_code, 200)
        reg.refresh_from_db()
        self.assertEqual(reg.state, "withdrawn")

    def test_past_event_rejects(self):
        self.event.date = date.today() - timedelta(days=1)
        self.event.save(update_fields=["date"])
        self.event.refresh_from_db()
        reg = Registration(
            event=self.event,
            organizer=self.alice,
            organizer_name="Alice",
            state="active",
        )
        reg.save()
        response = self._put(
            self._payload(self.alice, "withdrawn", self.event.version, reg.version)
        )
        self.assertEqual(response.status_code, 409)
        reg.refresh_from_db()
        self.assertEqual(reg.state, "active")

    def test_cancelled_event_rejects(self):
        reg = self._reg()
        event_services.cancel_event(self.event.pk, actor=BUREAU)
        self.event.refresh_from_db()
        response = self._put(
            self._payload(self.alice, "withdrawn", self.event.version, reg.version)
        )
        self.assertEqual(response.status_code, 409)
        reg.refresh_from_db()
        self.assertEqual(reg.state, "active")

    def test_archived_period_rejects(self):
        reg = self._reg()
        period_services.archive(self.period.pk, actor=BUREAU)
        self.event.refresh_from_db()
        response = self._put(
            self._payload(self.alice, "withdrawn", self.event.version, reg.version)
        )
        self.assertEqual(response.status_code, 409)
        reg.refresh_from_db()
        self.assertEqual(reg.state, "active")

    def test_hidden_event_rejects(self):
        reg = self._reg()
        event_services.set_event_hidden(
            self.event.pk,
            actor=BUREAU,
            is_hidden=True,
            expected_version=self.event.version,
        )
        self.event.refresh_from_db()
        response = self._put(
            self._payload(self.alice, "withdrawn", self.event.version, reg.version)
        )
        self.assertEqual(response.status_code, 409)
        reg.refresh_from_db()
        self.assertEqual(reg.state, "active")

    def test_full_open_permits_withdraw_then_signup(self):
        self.event.capacity = 1
        self.event.min_organizers = 1
        self.event.save(update_fields=["capacity", "min_organizers"])
        bob = organizers.get_or_create_organizer("Bob")
        reg = self._reg()
        blocked = self._put(self._payload(bob, "active", self.event.version, 0))
        self.assertEqual(blocked.status_code, 409)
        response = self._put(
            self._payload(self.alice, "withdrawn", self.event.version, reg.version)
        )
        self.assertEqual(response.status_code, 200)
        reg.refresh_from_db()
        self.assertEqual(reg.state, "withdrawn")
        self.event.refresh_from_db()
        response = self._put(self._payload(bob, "active", self.event.version, 0))
        self.assertEqual(response.status_code, 200)

    def test_csrf_required(self):
        reg = self._reg()
        client = Client(enforce_csrf_checks=True)
        response = client.put(
            self.url,
            json.dumps(self._payload(self.alice, "withdrawn", self.event.version, reg.version)),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        reg.refresh_from_db()
        self.assertEqual(reg.state, "active")

    def test_double_withdraw_idempotent(self):
        reg = self._reg()
        response = self._put(
            self._payload(self.alice, "withdrawn", self.event.version, reg.version)
        )
        self.assertEqual(response.status_code, 200)
        reg.refresh_from_db()
        version = reg.version
        self.event.refresh_from_db()
        response = self._put(
            self._payload(self.alice, "withdrawn", self.event.version, reg.version)
        )
        self.assertEqual(response.status_code, 200)
        reg.refresh_from_db()
        self.assertEqual(reg.version, version)
        self.assertEqual(
            Registration.objects.filter(event=self.event, organizer=self.alice).count(),
            1,
        )
