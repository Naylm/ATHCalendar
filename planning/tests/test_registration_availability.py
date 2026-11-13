import json
import uuid
from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase

from planning.models import Event, Registration
from planning.selectors import event_payload, public_event_interactive
from planning.services import events as event_services
from planning.services import organizers, registrations
from planning.services import periods as period_services
from planning.services.actors import Actor
from planning.tests import web_client

BUREAU = Actor.bureau()


def _bureau_client():
    group, _ = Group.objects.get_or_create(name="Bureau")
    user = User.objects.create_user(username="bureau", password="x")
    user.groups.add(group)
    client = Client()
    client.force_login(user)
    return client


def _fixture():
    today = date.today()
    period = period_services.create_period(
        actor=BUREAU,
        label="S1",
        start_date=today + timedelta(days=1),
        end_date=today + timedelta(days=30),
        creation_key=uuid.uuid4(),
    )
    event = Event.objects.order_by("date").first()
    event_services.set_registrations_open(event.pk, actor=BUREAU, is_open=False)
    event.refresh_from_db()
    alice = organizers.get_or_create_organizer("Alice")
    reg = registrations.set_registration(event.pk, alice.pk, "active", actor=Actor.public(alice))
    event.refresh_from_db()
    return {
        "period": period,
        "event": event,
        "alice": alice,
        "reg": reg,
    }


@web_client
class PublicAvailabilityPolicyTests(TestCase):
    def setUp(self):
        self.fx = _fixture()
        self.period = self.fx["period"]
        self.event = self.fx["event"]
        self.alice = self.fx["alice"]
        self.client = Client()

    def _reg_put(self, event, state, event_version, reg_version=0):
        return self.client.put(
            f"/api/events/{event.pk}/registration/",
            json.dumps(
                {
                    "organizer_id": str(self.alice.pk),
                    "state": state,
                    "expected_event_version": event_version,
                    "expected_version": reg_version,
                }
            ),
            content_type="application/json",
        )

    def _type_put(self, event, version):
        return self.client.put(
            f"/api/events/{event.pk}/type/",
            json.dumps({"game_type_id": None, "expected_event_version": version}),
            content_type="application/json",
        )

    def test_payload_interactive_despite_stored_flag(self):
        self.assertFalse(self.event.registrations_open)
        self.assertTrue(public_event_interactive(self.event))
        payload = event_payload(self.event)
        self.assertTrue(payload["interactive"])
        self.assertTrue(payload["registrations_open"])
        self.assertFalse(Event.objects.get(pk=self.event.pk).registrations_open)

    def test_index_row_open_with_form_and_remove(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        row = content.split(f'id="date-{self.event.pk}"', 1)[1].split("</li>", 1)[0]
        self.assertIn('data-open="true"', row)
        self.assertIn("name-registration-form", row)
        self.assertIn("event-type-select", row)
        self.assertIn("remove-registration", row)
        self.assertNotIn("Inscriptions fermées", row)
        self.assertNotIn("Fermée", row)

    def test_live_and_planning_api_effective_flag(self):
        response = self.client.get(f"/api/periods/{self.period.pk}/live/")
        self.assertEqual(response.status_code, 200)
        row = response.json()["html"].split(f'id="date-{self.event.pk}"', 1)[1]
        self.assertIn('data-open="true"', row.split("</li>", 1)[0])
        response = self.client.get(f"/api/periods/{self.period.pk}/planning/")
        events = {e["id"]: e for e in response.json()["events"]}
        self.assertTrue(events[str(self.event.pk)]["registrations_open"])

    def test_signup_withdraw_type_allowed_despite_flag(self):
        event = self.event
        event.refresh_from_db()
        reg = Registration.objects.get(event=event, organizer=self.alice)
        response = self._reg_put(event, "withdrawn", event.version, reg.version)
        self.assertEqual(response.status_code, 200)
        reg.refresh_from_db()
        self.assertEqual(reg.state, "withdrawn")
        event.refresh_from_db()
        response = self._reg_put(event, "active", event.version, reg.version)
        self.assertEqual(response.status_code, 200)
        event.refresh_from_db()
        response = self._type_put(event, event.version)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Event.objects.get(pk=event.pk).registrations_open)

    def test_batch_registration_allowed(self):
        event = self.event
        event.refresh_from_db()
        reg = Registration.objects.get(event=event, organizer=self.alice)
        response = self.client.post(
            "/api/registrations/batch/",
            json.dumps(
                {
                    "organizer_id": str(self.alice.pk),
                    "event_ids": [str(event.pk)],
                    "state": "withdrawn",
                    "versions": {
                        str(event.pk): {
                            "event": event.version,
                            "registration": reg.version,
                        }
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

    def test_hidden_cancelled_past_archived_still_block(self):
        event = self.event
        event.refresh_from_db()
        for lock in (
            lambda: event_services.set_event_hidden(
                event.pk,
                actor=BUREAU,
                is_hidden=True,
                expected_version=Event.objects.get(pk=event.pk).version,
            ),
            lambda: event_services.cancel_event(event.pk, actor=BUREAU),
        ):
            lock()
            event.refresh_from_db()
            version = event.version
            response = self._reg_put(event, "withdrawn", version, 0)
            self.assertEqual(response.status_code, 409)
            response = self._type_put(event, version)
            self.assertEqual(response.status_code, 409)
            event_services.restore_event(event.pk, actor=BUREAU)
            event_services.set_event_hidden(
                event.pk,
                actor=BUREAU,
                is_hidden=False,
                expected_version=Event.objects.get(pk=event.pk).version,
            )
            event.refresh_from_db()
        past = Event.objects.create(period=self.period, date=date.today() - timedelta(days=1))
        response = self._reg_put(past, "active", past.version, 0)
        self.assertEqual(response.status_code, 409)
        period_services.archive(self.period.pk, actor=BUREAU)
        event.refresh_from_db()
        response = self._reg_put(event, "withdrawn", event.version, 0)
        self.assertEqual(response.status_code, 409)

    def test_capacity_blocks_join_not_withdraw(self):
        event_services.update_event(self.event.pk, actor=BUREAU, min_organizers=1, capacity=1)
        bob = organizers.get_or_create_organizer("Bob")
        self.event.refresh_from_db()
        response = self.client.put(
            f"/api/events/{self.event.pk}/registration/",
            json.dumps(
                {
                    "organizer_id": str(bob.pk),
                    "state": "active",
                    "expected_event_version": self.event.version,
                    "expected_version": 0,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)
        reg = Registration.objects.get(event=self.event, organizer=self.alice)
        self.event.refresh_from_db()
        response = self._reg_put(self.event, "withdrawn", self.event.version, reg.version)
        self.assertEqual(response.status_code, 200)

    def test_stale_version_still_409(self):
        self.event.refresh_from_db()
        response = self._reg_put(self.event, "withdrawn", self.event.version + 5, 0)
        self.assertEqual(response.status_code, 409)
        reg = Registration.objects.get(event=self.event, organizer=self.alice)
        self.assertEqual(reg.state, "active")

    def test_organizer_registrations_flag_effective(self):
        response = self.client.get(f"/api/organizers/{self.alice.pk}/registrations/?history=1")
        regs = {r["event_id"]: r for r in response.json()["registrations"]}
        self.assertTrue(regs[str(self.event.pk)]["registrations_open"])

    def test_bureau_pages_have_no_closed_ui(self):
        client = _bureau_client()
        for url in (
            f"/bureau/periodes/{self.period.pk}/",
            f"/bureau/dates/{self.event.pk}/",
        ):
            content = client.get(url).content.decode()
            for text in (
                "Fermée",
                "Fermer les inscriptions",
                "Ouvrir les inscriptions",
                "Clôturer les inscriptions",
            ):
                self.assertNotIn(text, content, (url, text))

    def test_visibility_json_reports_effective_state(self):
        client = _bureau_client()
        event_services.set_event_hidden(
            self.event.pk,
            actor=BUREAU,
            is_hidden=True,
            expected_version=Event.objects.get(pk=self.event.pk).version,
        )
        self.period.refresh_from_db()
        target = Event.objects.get(pk=self.event.pk)
        response = client.post(
            f"/bureau/periodes/{self.period.pk}/",
            {
                "action": "visibility",
                "event_id": str(self.event.pk),
                "visible": "on",
                "staffing": "all",
                "expected_revision": self.period.revision,
                "expected_version": target.version,
            },
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["event"]["is_hidden"])
        self.assertTrue(body["event"]["registrations_open"])
        self.assertFalse(Event.objects.get(pk=self.event.pk).registrations_open)

    def test_legacy_close_open_actions_disabled(self):
        client = _bureau_client()
        self.period.refresh_from_db()
        for data in (
            {"action": "close_all"},
            {"action": "open_all"},
        ):
            response = client.post(f"/bureau/periodes/{self.period.pk}/", data)
            self.assertEqual(response.status_code, 302)
        for data in ({"action": "close"}, {"action": "open"}):
            response = client.post(f"/bureau/dates/{self.event.pk}/", data)
            self.assertEqual(response.status_code, 302)
        self.assertFalse(Event.objects.get(pk=self.event.pk).registrations_open)

    def test_finalize_url_disabled(self):
        client = _bureau_client()
        url = f"/bureau/periodes/{self.period.pk}/cloture/"
        self.period.refresh_from_db()
        revision = self.period.revision
        for method in (client.get, client.post):
            response = method(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn(f"/bureau/periodes/{self.period.pk}/", response["Location"])
        self.period.refresh_from_db()
        self.assertEqual(self.period.revision, revision)
        self.assertFalse(Event.objects.filter(is_hidden=True).exists())

    def test_public_signup_on_restored_event(self):
        event_services.set_event_hidden(
            self.event.pk,
            actor=BUREAU,
            is_hidden=True,
            expected_version=Event.objects.get(pk=self.event.pk).version,
        )
        event_services.set_event_hidden(
            self.event.pk,
            actor=BUREAU,
            is_hidden=False,
            expected_version=Event.objects.get(pk=self.event.pk).version,
        )
        bob = organizers.get_or_create_organizer("Bob")
        self.event.refresh_from_db()
        response = self.client.put(
            f"/api/events/{self.event.pk}/registration/",
            json.dumps(
                {
                    "organizer_id": str(bob.pk),
                    "state": "active",
                    "expected_event_version": self.event.version,
                    "expected_version": 0,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Event.objects.get(pk=self.event.pk).registrations_open)
        self.assertTrue(
            Registration.objects.filter(event=self.event, organizer=bob, state="active").exists()
        )
