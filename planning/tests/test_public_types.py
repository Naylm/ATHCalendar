import json
import uuid
from datetime import date, timedelta

from django.test import Client, TestCase

from planning.models import AuditEntry, Event, GameType
from planning.services import events, organizers, periods, registrations
from planning.services.actors import Actor
from planning.tests import web_client

BUREAU = Actor.bureau()


@web_client
class PublicTypeApiTests(TestCase):
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
        self.flex = GameType.objects.get(name="Flex")
        self.roltac = GameType.objects.get(name="Roltac")
        self.url = f"/api/events/{self.event.pk}/type/"

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

    def test_anonymous_sets_type(self):
        version = self._version()
        revision = self._revision()
        response = self._put({"game_type_id": str(self.flex.pk), "expected_event_version": version})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["event"]["type_label"], "Flex")
        self.event.refresh_from_db()
        self.assertEqual(self.event.game_type_id, self.flex.pk)
        self.assertEqual(self.event.type_label, "Flex")
        self.assertEqual(self.event.version, version + 1)
        self.assertEqual(self._revision(), revision + 1)
        entry = AuditEntry.objects.filter(
            action="event.type_public", target_id=str(self.event.pk)
        ).get()
        self.assertEqual(entry.actor_type, AuditEntry.ActorType.PUBLIC)
        self.assertEqual(entry.after["game_type_id"], str(self.flex.pk))

    def test_only_target_day_changes(self):
        other = self.period.events.order_by("date")[1]
        version = self._version()
        self._put({"game_type_id": str(self.flex.pk), "expected_event_version": version})
        other.refresh_from_db()
        self.assertIsNone(other.game_type_id)
        self.assertEqual(other.type_label, "")

    def test_change_type_then_clear(self):
        self._put({"game_type_id": str(self.flex.pk), "expected_event_version": self._version()})
        response = self._put(
            {"game_type_id": str(self.roltac.pk), "expected_event_version": self._version()}
        )
        self.assertEqual(response.status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(self.event.game_type_id, self.roltac.pk)
        response = self._put({"game_type_id": None, "expected_event_version": self._version()})
        self.assertEqual(response.status_code, 200)
        self.event.refresh_from_db()
        self.assertIsNone(self.event.game_type_id)
        self.assertEqual(self.event.type_label, "")

    def test_same_type_noop(self):
        events.update_event(self.event.pk, actor=BUREAU, game_type_id=str(self.flex.pk))
        version = self._version()
        revision = self._revision()
        before = AuditEntry.objects.count()
        response = self._put({"game_type_id": str(self.flex.pk), "expected_event_version": version})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._version(), version)
        self.assertEqual(self._revision(), revision)
        self.assertEqual(AuditEntry.objects.count(), before)

    def test_missing_and_invalid_version_400(self):
        for body in (
            {"game_type_id": str(self.flex.pk)},
            {"expected_event_version": self._version()},
            {"game_type_id": str(self.flex.pk), "expected_event_version": "abc"},
            {"game_type_id": str(self.flex.pk), "expected_event_version": True},
            {"game_type_id": str(self.flex.pk), "expected_event_version": 0},
        ):
            self.assertEqual(self._put(body).status_code, 400, body)

    def test_stale_version_409(self):
        self._put({"game_type_id": str(self.flex.pk), "expected_event_version": self._version()})
        stale = self._version() - 1
        response = self._put({"game_type_id": str(self.roltac.pk), "expected_event_version": stale})
        self.assertEqual(response.status_code, 409)
        self.event.refresh_from_db()
        self.assertEqual(self.event.game_type_id, self.flex.pk)

    def test_invalid_type_values_400(self):
        for value in ("", "not-a-uuid", 5, True, str(uuid.uuid4())):
            self.assertEqual(
                self._put(
                    {"game_type_id": value, "expected_event_version": self._version()}
                ).status_code,
                400,
                value,
            )

    def test_inactive_type_400(self):
        inactive = GameType.objects.create(name="Inactif", is_active=False)
        self.assertEqual(
            self._put(
                {"game_type_id": str(inactive.pk), "expected_event_version": self._version()}
            ).status_code,
            400,
        )

    def test_inactive_current_type_400_unchanged(self):
        inactive = GameType.objects.create(name="Inactif", is_active=False)
        Event.objects.filter(pk=self.event.pk).update(game_type=inactive, type_label="Inactif")
        self.event.refresh_from_db()
        version = self.event.version
        revision = self._revision()
        response = self._put({"game_type_id": str(inactive.pk), "expected_event_version": version})
        self.assertEqual(response.status_code, 400)
        self.event.refresh_from_db()
        self.assertEqual(self.event.version, version)
        self.assertEqual(self._revision(), revision)

    def test_extra_and_privileged_keys_400(self):
        for extra in ("title", "actor", "min_organizers"):
            body = {
                "game_type_id": str(self.flex.pk),
                "expected_event_version": self._version(),
                extra: "x",
            }
            self.assertEqual(self._put(body).status_code, 400, extra)

    def test_closed_event_409(self):
        events.set_registrations_open(self.event.pk, actor=BUREAU, is_open=False)
        response = self._put(
            {"game_type_id": str(self.flex.pk), "expected_event_version": self._version()}
        )
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(Event.objects.get(pk=self.event.pk).game_type_id)

    def test_cancelled_event_409(self):
        events.cancel_event(self.event.pk, actor=BUREAU)
        response = self._put(
            {"game_type_id": str(self.flex.pk), "expected_event_version": self._version()}
        )
        self.assertEqual(response.status_code, 409)

    def test_past_event_409(self):
        today = date.today()
        past_period = periods.create_period(
            actor=BUREAU,
            label="Passée",
            start_date=today - timedelta(days=60),
            end_date=today - timedelta(days=1),
            creation_key=uuid.uuid4(),
        )
        event = past_period.events.order_by("date").first()
        response = self._put(
            {"game_type_id": str(self.flex.pk), "expected_event_version": event.version},
            url=f"/api/events/{event.pk}/type/",
        )
        self.assertEqual(response.status_code, 409)

    def test_archived_period_409(self):
        periods.archive(self.period.pk, actor=BUREAU)
        response = self._put(
            {"game_type_id": str(self.flex.pk), "expected_event_version": self._version()}
        )
        self.assertEqual(response.status_code, 409)

    def test_unknown_event_404(self):
        response = self._put(
            {"game_type_id": str(self.flex.pk), "expected_event_version": 1},
            url=f"/api/events/{uuid.uuid4()}/type/",
        )
        self.assertEqual(response.status_code, 404)

    def test_other_methods_405(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(self.client.post(self.url).status_code, 405)
        self.assertEqual(self.client.delete(self.url).status_code, 405)

    def test_csrf_required(self):
        client = Client(enforce_csrf_checks=True)
        response = self._put(
            {"game_type_id": str(self.flex.pk), "expected_event_version": self._version()},
            client=client,
        )
        self.assertEqual(response.status_code, 403)
        client.get("/")
        csrf = client.cookies.get("csrftoken")
        response = client.put(
            self.url,
            data=json.dumps(
                {"game_type_id": str(self.flex.pk), "expected_event_version": self._version()}
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf.value,
        )
        self.assertEqual(response.status_code, 200)

    def test_other_fields_unchanged(self):
        organizer = organizers.get_or_create_organizer("Alice")
        registrations.set_registration(
            self.event.pk, organizer.pk, "active", actor=Actor.public(organizer)
        )
        title = self.event.title
        day = self.event.date
        self._put({"game_type_id": str(self.flex.pk), "expected_event_version": self._version()})
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, title)
        self.assertEqual(self.event.date, day)
        self.assertEqual(self.event.registrations.count(), 1)

    def test_rate_limit(self):
        from django.conf import settings as django_settings

        limit = django_settings.PUBLIC_WRITE_LIMIT
        for i in range(limit):
            target = self.flex if i % 2 == 0 else self.roltac
            self.assertEqual(
                self._put(
                    {
                        "game_type_id": str(target.pk),
                        "expected_event_version": self._version(),
                    }
                ).status_code,
                200,
            )
        response = self._put(
            {"game_type_id": str(self.flex.pk), "expected_event_version": self._version()}
        )
        self.assertEqual(response.status_code, 429)
