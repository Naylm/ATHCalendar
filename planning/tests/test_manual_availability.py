import json
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.utils import timezone

from planning.errors import (
    ArchivedConflict,
    ClosedConflict,
    Forbidden,
    ValidationFailed,
    VersionConflict,
)
from planning.models import AuditEntry, Event, Period, Registration
from planning.selectors import public_event_interactive
from planning.services import events, organizers, registrations
from planning.services.actors import Actor
from planning.services.periods import paris_today
from planning.tests import web_client

BUREAU = Actor.bureau()


@web_client
class ManualAvailabilityTests(TestCase):
    def setUp(self):
        today = paris_today()
        self.period = Period.objects.create(
            creation_key=uuid.uuid4(),
            label="Disponibilités",
            start_date=today,
            end_date=today + timedelta(days=30),
        )
        self.event = Event.objects.create(period=self.period, date=today + timedelta(days=2))
        self.alice = organizers.get_or_create_organizer("Alice")
        self.reg = registrations.set_registration(
            self.event.pk, self.alice.pk, "active", actor=Actor.public(self.alice)
        )

    def change(self, value, **kwargs):
        self.event.refresh_from_db()
        return events.set_event_availability(
            self.event.pk,
            actor=kwargs.get("actor", BUREAU),
            availability=value,
            expected_version=kwargs.get("version", self.event.version),
        )

    def test_change_is_versioned_audited_and_does_not_change_registrations(self):
        self.period.refresh_from_db()
        revision = self.period.revision
        before = list(Registration.objects.values())
        result = self.change("full")
        self.assertEqual(result.version, 2)
        self.period.refresh_from_db()
        self.assertEqual(self.period.revision, revision + 1)
        self.assertEqual(list(Registration.objects.values()), before)
        entry = AuditEntry.objects.get(action="event.availability")
        self.assertEqual(entry.before["availability"], "available")
        self.assertEqual(entry.after["availability"], "full")
        self.change("full")
        self.assertEqual(AuditEntry.objects.filter(action="event.availability").count(), 1)
        self.assertEqual(Event.objects.get(pk=self.event.pk).version, 2)

    def test_permissions_validation_and_stale_versions(self):
        with self.assertRaises(Forbidden):
            self.change("full", actor=Actor.public(self.alice))
        for value in ("invalid", None, True):
            with self.subTest(value=value), self.assertRaises(ValidationFailed):
                self.change(value)
        for version in (True, 0, "1"):
            with self.subTest(version=version), self.assertRaises(ValidationFailed):
                self.change("full", version=version)
        self.change("full")
        with self.assertRaises(VersionConflict):
            self.change("available", version=1)

    def test_archived_period_blocks_even_bureau_noop(self):
        self.period.archived_at = timezone.now()
        self.period.save(update_fields=["archived_at"])
        with self.assertRaises(ArchivedConflict):
            self.change("available")

    def test_manual_states_lock_public_changes_but_not_bureau_corrections(self):
        for value in ("full", "unavailable"):
            self.change(value)
            for state in ("active", "withdrawn"):
                with self.subTest(value=value, state=state), self.assertRaises(ClosedConflict):
                    registrations.set_registration(
                        self.event.pk,
                        self.alice.pk,
                        state,
                        actor=Actor.public(self.alice),
                        note="Essai",
                    )
            with self.assertRaises(ClosedConflict):
                events.set_public_game_type(
                    self.event.pk,
                    actor=Actor.public(self.alice),
                    game_type_id=None,
                    expected_version=self.event.version,
                )
        registrations.set_registration(
            self.event.pk, self.alice.pk, "active", actor=BUREAU, note="Correction"
        )
        self.reg.refresh_from_db()
        self.assertEqual(self.reg.note, "Correction")
        self.change("available")
        self.event.refresh_from_db()
        self.assertTrue(public_event_interactive(self.event))
        registrations.set_registration(
            self.event.pk, self.alice.pk, "withdrawn", actor=Actor.public(self.alice)
        )

    def test_stale_open_api_page_cannot_bypass_the_lock(self):
        old_version = self.event.version
        self.change("full")
        for version in (old_version, old_version + 1):
            response = Client().put(
                f"/api/events/{self.event.pk}/registration/",
                data=json.dumps(
                    {
                        "organizer_id": str(self.alice.pk),
                        "state": "withdrawn",
                        "expected_event_version": version,
                        "expected_version": self.reg.version,
                    }
                ),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 409)
        self.reg.refresh_from_db()
        self.assertEqual(self.reg.state, "active")

    def test_mixed_batch_is_atomic(self):
        other = Event.objects.create(period=self.period, date=self.event.date + timedelta(days=1))
        bob = organizers.get_or_create_organizer("Bob")
        self.change("unavailable")
        self.event.refresh_from_db()
        response = Client().post(
            "/api/registrations/batch/",
            data=json.dumps(
                {
                    "organizer_id": str(bob.pk),
                    "event_ids": [str(self.event.pk), str(other.pk)],
                    "state": "active",
                    "versions": {
                        str(self.event.pk): {"event": self.event.version, "registration": 0},
                        str(other.pk): {"event": other.version, "registration": 0},
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertFalse(Registration.objects.filter(organizer=bob).exists())

    def test_public_views_keep_names_and_hide_actions_on_locked_date(self):
        self.change("full")
        response = Client().get("/")
        self.assertContains(response, f"Version {settings.APP_VERSION}")
        row = (
            response.content.decode().split(f'id="date-{self.event.pk}"', 1)[1].split("</li>", 1)[0]
        )
        self.assertIn("Complète", row)
        self.assertIn("Alice", row)
        self.assertIn('data-open="false"', row)
        for token in ("name-registration-form", "remove-registration", "event-type-select"):
            self.assertNotIn(token, row)
        live = Client().get(f"/api/periods/{self.period.pk}/live/").json()
        self.assertIn("Complète", live["html"])
        payload = Client().get(f"/api/periods/{self.period.pk}/planning/").json()
        self.assertEqual(payload["events"][0]["availability"], "full")
        self.assertFalse(payload["events"][0]["interactive"])
        reinforcement = (
            Client().get(f"/api/periods/{self.period.pk}/planning/?filtre=renfort").json()
        )
        self.assertEqual(reinforcement["events"], [])

    def test_bureau_post_and_safe_redirect(self):
        group, _ = Group.objects.get_or_create(name="Bureau")
        user = User.objects.create_user(username="bureau")
        user.groups.add(group)
        client = Client()
        client.force_login(user)
        response = client.post(
            f"/bureau/dates/{self.event.pk}/",
            {
                "action": "availability",
                "availability": "unavailable",
                "expected_version": self.event.version,
                "return_to_period": "1",
                "next": "https://example.invalid",
            },
        )
        self.assertRedirects(response, f"/bureau/periodes/{self.period.pk}/")
        self.event.refresh_from_db()
        self.assertEqual(self.event.availability, "unavailable")
        self.assertContains(client.get(f"/bureau/periodes/{self.period.pk}/"), 'value="full"')

    def test_hide_and_cancel_restore_do_not_silently_reopen_a_date(self):
        self.change("full")
        events.cancel_event(self.event.pk, actor=BUREAU)
        events.restore_event(self.event.pk, actor=BUREAU)
        self.event.refresh_from_db()
        events.set_event_hidden(
            self.event.pk, actor=BUREAU, is_hidden=True, expected_version=self.event.version
        )
        self.event.refresh_from_db()
        events.set_event_hidden(
            self.event.pk, actor=BUREAU, is_hidden=False, expected_version=self.event.version
        )
        self.event.refresh_from_db()
        self.assertEqual(self.event.availability, "full")
        self.assertFalse(public_event_interactive(self.event))
