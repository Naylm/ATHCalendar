import json
import uuid
from contextlib import contextmanager
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.utils import timezone

from planning.db import write_transaction
from planning.models import AuditEntry, Event, Registration
from planning.services import events, periods
from planning.services.actors import Actor
from planning.tests import web_client

BUREAU = Actor.bureau()


@web_client
class PublicNoteApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        today = date.today()
        self.period = periods.create_period(
            actor=BUREAU,
            label="S1 notes",
            start_date=today + timedelta(days=1),
            end_date=today + timedelta(days=120),
            creation_key=uuid.uuid4(),
        )
        self.event = self.period.events.order_by("date").first()
        self.other = self.period.events.order_by("date")[1]
        self.url = f"/api/events/{self.event.pk}/note/"

    def _put(self, body, client=None, url=None):
        return (client or self.client).put(
            url or self.url,
            data=json.dumps(body),
            content_type="application/json",
        )

    def _version(self):
        return Event.objects.get(pk=self.event.pk).version

    def _revision(self):
        self.period.refresh_from_db()
        return self.period.revision

    def test_anonymous_writes_and_clears_note_with_unicode_newlines(self):
        version = self._version()
        revision = self._revision()
        note = "\nBesoin d’un/une expérimenté(e)  \nDeuxième ligne éàü"
        response = self._put({"public_note": note, "expected_event_version": version})
        self.assertEqual(response.status_code, 200)
        self.event.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.event.public_note, note)
        self.assertEqual(self.event.version, version + 1)
        self.assertEqual(self._revision(), revision + 1)
        self.assertEqual(response.json()["event"]["public_note"], note)
        entry = AuditEntry.objects.get(action="event.note_public", target_id=str(self.event.pk))
        self.assertEqual(entry.actor_type, AuditEntry.ActorType.PUBLIC)
        self.assertEqual(entry.before["public_note"], "")
        self.assertEqual(entry.after["public_note"], note)
        # Autre date inchangée.
        self.assertIsNone(Registration.objects.filter(event=self.other).first())
        untouched = self.period.events.exclude(pk=self.event.pk)
        for event in untouched:
            self.assertEqual(event.version, 1)
            self.assertEqual(event.public_note, "")
        response = self._put({"public_note": "", "expected_event_version": self._version()})
        self.assertEqual(response.status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(self.event.public_note, "")

    def test_same_value_is_noop_but_stale_version_rejects(self):
        response = self._put({"public_note": "", "expected_event_version": self._version()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._version(), 1)
        self.assertFalse(AuditEntry.objects.filter(action="event.note_public").exists())
        response = self._put({"public_note": "", "expected_event_version": 999})
        self.assertEqual(response.status_code, 409)

    def test_two_competing_writes_first_wins(self):
        version = self._version()
        first = self._put({"public_note": "premier", "expected_event_version": version})
        second = self._put({"public_note": "second", "expected_event_version": version})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        self.event.refresh_from_db()
        self.assertEqual(self.event.public_note, "premier")

    def test_locked_states_reject_even_for_bureau_session(self):
        group, _ = Group.objects.get_or_create(name="Bureau")
        user = User.objects.create_user(username="bureau")
        user.groups.add(group)
        bureau = Client()
        bureau.force_login(user)
        base = {"expected_event_version": 1, "public_note": "x"}
        # passée
        past_period = periods.create_period(
            actor=BUREAU,
            label="Passée",
            start_date=date.today() - timedelta(days=10),
            end_date=date.today() - timedelta(days=5),
            creation_key=uuid.uuid4(),
        )
        past_event = past_period.events.first()
        self.assertEqual(
            self._put(base, url=f"/api/events/{past_event.pk}/note/").status_code,
            409,
        )
        for index, field in enumerate(
            (
                {"status": Event.Status.CANCELLED},
                {"is_hidden": True},
                {"availability": Event.Availability.FULL},
                {"availability": Event.Availability.UNAVAILABLE},
            )
        ):
            event = Event.objects.create(period=self.period, date=date(2031, 6, 1 + index), **field)
            response = bureau.put(
                f"/api/events/{event.pk}/note/",
                data=json.dumps(base),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 409, field)
            event.refresh_from_db()
            self.assertEqual(event.public_note, "")
        # archivée : la période entière est verrouillée
        self.period.archived_at = timezone.now()
        self.period.save(update_fields=["archived_at"])
        self.assertEqual(
            self._put({"expected_event_version": self._version(), "public_note": "x"}).status_code,
            409,
        )

    def test_bureau_can_still_correct_via_update_event(self):
        event = Event.objects.create(
            period=self.period,
            date=date.today() + timedelta(days=40),
            availability=Event.Availability.UNAVAILABLE,
        )
        updated = events.update_event(event.pk, actor=BUREAU, public_note="correction")
        self.assertEqual(updated.public_note, "correction")

    def test_invalid_payloads_rejected_without_mutation(self):
        version = self._version()
        cases = [
            {},
            {"public_note": "x"},
            {"expected_event_version": version},
            {"public_note": None, "expected_event_version": version},
            {"public_note": 5, "expected_event_version": version},
            {"public_note": True, "expected_event_version": version},
            {"public_note": ["a"], "expected_event_version": version},
            {"public_note": "x" * 1001, "expected_event_version": version},
            {"public_note": "x", "expected_event_version": 0},
            {"public_note": "x", "expected_event_version": "abc"},
            {"public_note": "x", "expected_event_version": True},
            {"public_note": "x", "expected_event_version": version, "extra": 1},
            {"public_note": "x", "expected_event_version": version, "role": "bureau"},
        ]
        for body in cases:
            response = self._put(body)
            self.assertEqual(response.status_code, 400, body)
        self.assertEqual(self._version(), version)
        self.event.refresh_from_db()
        self.assertEqual(self.event.public_note, "")
        response = self._put({"public_note": "x" * 1000, "expected_event_version": version})
        self.assertEqual(response.status_code, 200)

    def test_event_deleted_between_reads_returns_404_not_500(self):
        @contextmanager
        def deleting_transaction():
            with write_transaction():
                Event.objects.filter(pk=self.event.pk).delete()
                yield

        with patch("planning.services.events.write_transaction", deleting_transaction):
            response = self._put({"public_note": "x", "expected_event_version": self._version()})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(AuditEntry.objects.filter(action="event.note_public").exists())

    def test_csrf_and_method_guards(self):
        csrf = Client(enforce_csrf_checks=True)
        self.assertEqual(
            csrf.put(
                self.url,
                data=json.dumps({"public_note": "x", "expected_event_version": 1}),
                content_type="application/json",
            ).status_code,
            403,
        )
        for method in (self.client.get, self.client.post, self.client.delete):
            self.assertEqual(method(self.url).status_code, 405)
        self.assertEqual(self._version(), 1)

    def test_rendered_form_is_escaped_and_locked_row_stays_readonly(self):
        events.update_event(
            self.event.pk, actor=BUREAU, public_note="</textarea><script>alert(1)</script>"
        )
        Event.objects.create(
            period=self.period,
            date=date(2031, 6, 8),
            availability=Event.Availability.FULL,
            public_note="Lecture seule",
        )
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "event-note-form")
        self.assertNotContains(response, "</textarea><script>")
        self.assertContains(response, "&lt;/textarea&gt;")
        self.assertContains(response, '<p class="event-note">Lecture seule</p>')
