import json
import uuid
from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase

from planning.errors import (
    ArchivedConflict,
    ClosedConflict,
    Conflict,
    Forbidden,
    ValidationFailed,
    VersionConflict,
)
from planning.models import AuditEntry, Event
from planning.services import events as event_services
from planning.services import organizers, registrations
from planning.services import periods as period_services
from planning.services.actors import Actor
from planning.tests import web_client

BUREAU = Actor.bureau()


def _make_period(days=60):
    today = date.today()
    return period_services.create_period(
        actor=BUREAU,
        label="S1 test",
        start_date=today + timedelta(days=1),
        end_date=today + timedelta(days=days),
        creation_key=uuid.uuid4(),
    )


def _bureau_client():
    group, _ = Group.objects.get_or_create(name="Bureau")
    user = User.objects.create_user(username="bureau", password="x")
    user.groups.add(group)
    client = Client()
    client.force_login(user)
    return client


@web_client
class FinalizePeriodServiceTests(TestCase):
    def setUp(self):
        self.period = _make_period()
        self.events = list(Event.objects.order_by("date"))

    def _revision(self):
        self.period.refresh_from_db()
        return self.period.revision

    def test_keep_hides_others_and_closes_all_future(self):
        alice = organizers.get_or_create_organizer("Alice")
        keep = self.events[0]
        cancel = self.events[1]
        hide = self.events[2]
        past = self.events[3]
        past.date = date.today() - timedelta(days=1)
        past.save(update_fields=["date"])
        event_services.cancel_event(cancel.pk, actor=BUREAU)
        reg = registrations.set_registration(
            keep.pk, alice.pk, "active", actor=Actor.public(alice), note="n1"
        )
        version = keep.version
        period_services.finalize_period(
            self.period.pk,
            actor=BUREAU,
            keep_event_ids=[str(keep.pk)],
            expected_revision=self._revision(),
        )
        keep.refresh_from_db()
        cancel.refresh_from_db()
        hide.refresh_from_db()
        past.refresh_from_db()
        self.assertFalse(keep.is_hidden)
        self.assertFalse(keep.registrations_open)
        self.assertEqual(keep.version, version + 1)
        self.assertTrue(cancel.is_hidden)
        self.assertEqual(cancel.status, Event.Status.CANCELLED)
        self.assertTrue(hide.is_hidden)
        self.assertFalse(hide.registrations_open)
        self.assertFalse(past.is_hidden)
        self.assertTrue(past.registrations_open)
        reg.refresh_from_db()
        self.assertEqual(reg.state, "active")
        self.assertEqual(reg.note, "n1")
        self.assertEqual(reg.organizer_name, "Alice")
        self.assertTrue(AuditEntry.objects.filter(action="event.finalize").exists())
        self.assertTrue(AuditEntry.objects.filter(action="period.finalize").exists())

    def test_invalid_keep_ids_roll_back_everything(self):
        past = self.events[0]
        past.date = date.today() - timedelta(days=1)
        past.save(update_fields=["date"])
        cancelled = self.events[1]
        event_services.cancel_event(cancelled.pk, actor=BUREAU)
        foreign = uuid.uuid4()
        today = date.today()
        other_period = period_services.create_period(
            actor=BUREAU,
            label="S2 test",
            start_date=today + timedelta(days=200),
            end_date=today + timedelta(days=260),
            creation_key=uuid.uuid4(),
        )
        foreign_in_other = other_period.events.order_by("date").first()
        revision = self._revision()
        for bad in (
            [str(past.pk)],
            [str(cancelled.pk)],
            [str(foreign)],
            [str(foreign_in_other.pk)],
        ):
            with self.assertRaises(ValidationFailed, msg=bad):
                period_services.finalize_period(
                    self.period.pk,
                    actor=BUREAU,
                    keep_event_ids=bad,
                    expected_revision=revision,
                )
            self.assertEqual(self._revision(), revision)
            self.assertEqual(Event.objects.filter(is_hidden=True).count(), 0)
            self.assertEqual(Event.objects.filter(registrations_open=False).count(), 1)

    def test_stale_revision_after_signup_closes_nothing(self):
        alice = organizers.get_or_create_organizer("Alice")
        revision = self._revision()
        registrations.set_registration(
            self.events[0].pk, alice.pk, "active", actor=Actor.public(alice)
        )
        with self.assertRaises(VersionConflict):
            period_services.finalize_period(
                self.period.pk,
                actor=BUREAU,
                keep_event_ids=[str(e.pk) for e in self.events],
                expected_revision=revision,
            )
        self.assertEqual(Event.objects.filter(is_hidden=True).count(), 0)
        self.assertEqual(
            Event.objects.filter(registrations_open=True).count(),
            len(self.events),
        )

    def test_archived_rejected(self):
        period_services.archive(self.period.pk, actor=BUREAU)
        with self.assertRaises(ArchivedConflict):
            period_services.finalize_period(
                self.period.pk,
                actor=BUREAU,
                keep_event_ids=[],
                expected_revision=self._revision(),
            )

    def test_non_bureau_rejected(self):
        alice = organizers.get_or_create_organizer("Alice")
        with self.assertRaises(Forbidden):
            period_services.finalize_period(
                self.period.pk,
                actor=Actor.public(alice),
                keep_event_ids=[],
                expected_revision=self._revision(),
            )

    def test_second_identical_call_is_noop(self):
        keep = str(self.events[0].pk)
        period_services.finalize_period(
            self.period.pk,
            actor=BUREAU,
            keep_event_ids=[keep],
            expected_revision=self._revision(),
        )
        revision = self._revision()
        versions = {str(e.pk): e.version for e in Event.objects.order_by("date")}
        audits = AuditEntry.objects.count()
        period_services.finalize_period(
            self.period.pk,
            actor=BUREAU,
            keep_event_ids=[keep],
            expected_revision=revision,
        )
        self.assertEqual(self._revision(), revision)
        self.assertEqual(
            {str(e.pk): e.version for e in Event.objects.order_by("date")},
            versions,
        )
        self.assertEqual(AuditEntry.objects.count(), audits)

    def test_empty_selection_allowed(self):
        period_services.finalize_period(
            self.period.pk,
            actor=BUREAU,
            keep_event_ids=[],
            expected_revision=self._revision(),
        )
        self.assertEqual(Event.objects.filter(is_hidden=True).count(), len(self.events))

    def test_malformed_revision_rejected(self):
        for bad in (None, "abc", -1, True, 1.5):
            with self.assertRaises(ValidationFailed, msg=bad):
                period_services.finalize_period(
                    self.period.pk,
                    actor=BUREAU,
                    keep_event_ids=[],
                    expected_revision=bad,
                )

    def test_finalize_closes_then_unhide_keeps_closed_and_data(self):
        alice = organizers.get_or_create_organizer("Alice")
        hidden = self.events[0]
        reg = registrations.set_registration(
            hidden.pk, alice.pk, "active", actor=Actor.public(alice)
        )
        period_services.finalize_period(
            self.period.pk,
            actor=BUREAU,
            keep_event_ids=[str(e.pk) for e in self.events[1:]],
            expected_revision=self._revision(),
        )
        hidden.refresh_from_db()
        self.assertTrue(hidden.is_hidden)
        self.assertFalse(hidden.registrations_open)
        event_services.set_event_hidden(
            hidden.pk,
            actor=BUREAU,
            is_hidden=False,
            expected_version=hidden.version,
        )
        hidden.refresh_from_db()
        self.assertFalse(hidden.is_hidden)
        self.assertFalse(hidden.registrations_open)
        reg.refresh_from_db()
        self.assertEqual(reg.state, "active")
        self.assertEqual(reg.organizer_name, "Alice")

    def test_unhide_stale_version_conflict(self):
        period_services.finalize_period(
            self.period.pk,
            actor=BUREAU,
            keep_event_ids=[],
            expected_revision=self._revision(),
        )
        hidden = Event.objects.order_by("date").first()
        with self.assertRaises(VersionConflict):
            event_services.set_event_hidden(
                hidden.pk,
                actor=BUREAU,
                is_hidden=False,
                expected_version=hidden.version + 9,
            )
        hidden.refresh_from_db()
        self.assertTrue(hidden.is_hidden)

    def test_open_all_skips_hidden(self):
        keep = self.events[0]
        period_services.finalize_period(
            self.period.pk,
            actor=BUREAU,
            keep_event_ids=[str(keep.pk)],
            expected_revision=self._revision(),
        )
        period_services.set_period_registrations(self.period.pk, actor=BUREAU, is_open=True)
        keep.refresh_from_db()
        self.assertTrue(keep.registrations_open)
        hidden = Event.objects.filter(is_hidden=True).first()
        self.assertFalse(hidden.registrations_open)

    def test_set_open_on_hidden_conflict(self):
        hidden = self.events[0]
        event_services.set_event_hidden(
            hidden.pk,
            actor=BUREAU,
            is_hidden=True,
            expected_version=hidden.version,
        )
        with self.assertRaises(ClosedConflict):
            event_services.set_registrations_open(hidden.pk, actor=BUREAU, is_open=True)

    def test_public_writes_denied_on_hidden(self):
        alice = organizers.get_or_create_organizer("Alice")
        hidden = self.events[0]
        reg = registrations.set_registration(
            hidden.pk, alice.pk, "active", actor=Actor.public(alice)
        )
        event_services.set_event_hidden(
            hidden.pk,
            actor=BUREAU,
            is_hidden=True,
            expected_version=hidden.version,
        )
        hidden.refresh_from_db()
        with self.assertRaises(ClosedConflict):
            registrations.set_registration(
                hidden.pk, alice.pk, "withdrawn", actor=Actor.public(alice)
            )
        with self.assertRaises(ClosedConflict):
            registrations.set_registration(hidden.pk, alice.pk, "active", actor=Actor.public(alice))
        with self.assertRaises(ClosedConflict):
            event_services.set_public_game_type(
                hidden.pk,
                actor=Actor.public(alice),
                game_type_id=None,
                expected_version=hidden.version,
            )
        with self.assertRaises(Conflict):
            registrations.batch_set(
                [str(hidden.pk)],
                alice.pk,
                "withdrawn",
                actor=Actor.public(alice),
            )
        reg.refresh_from_db()
        self.assertEqual(reg.state, "active")


@web_client
class FinalizePeriodViewTests(TestCase):
    def setUp(self):
        self.period = _make_period()
        self.events = list(Event.objects.order_by("date"))
        self.url = f"/bureau/periodes/{self.period.pk}/cloture/"

    def _revision(self):
        self.period.refresh_from_db()
        return self.period.revision

    def test_anonymous_redirected_to_login(self):
        response = Client().get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/bureau/connexion/", response["Location"])

    def test_get_redirects_to_period_detail(self):
        client = _bureau_client()
        revision = self._revision()
        response = client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/bureau/periodes/{self.period.pk}/", response["Location"])
        self.assertEqual(self._revision(), revision)
        self.assertEqual(Event.objects.filter(is_hidden=True).count(), 0)

    def test_post_redirects_without_mutation(self):
        client = _bureau_client()
        revision = self._revision()
        keep = self.events[0]
        response = client.post(
            self.url,
            {
                "keep_event_ids": [str(keep.pk)],
                "expected_revision": str(revision),
                "confirm_close": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/bureau/periodes/{self.period.pk}/", response["Location"])
        self.assertEqual(self._revision(), revision)
        self.assertEqual(Event.objects.filter(is_hidden=True).count(), 0)
        self.assertEqual(Event.objects.filter(registrations_open=False).count(), 0)

    def test_csrf_required(self):
        _bureau_client()
        csrf_client = Client(enforce_csrf_checks=True)
        group_user = User.objects.get(username="bureau")
        csrf_client.force_login(group_user)
        response = csrf_client.post(
            self.url,
            {
                "keep_event_ids": [str(self.events[0].pk)],
                "expected_revision": str(self._revision()),
                "confirm_close": "on",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Event.objects.filter(is_hidden=True).count(), 0)


@web_client
class HiddenPublicVisibilityTests(TestCase):
    def setUp(self):
        self.period = _make_period()
        self.events = list(Event.objects.order_by("date"))
        self.hidden = self.events[0]
        event_services.set_event_hidden(
            self.hidden.pk,
            actor=BUREAU,
            is_hidden=True,
            expected_version=self.hidden.version,
        )
        self.hidden.refresh_from_db()

    def test_hidden_excluded_from_index_and_live_and_api(self):
        client = Client()
        response = client.get("/?filtre=toutes")
        self.assertNotContains(response, f'id="date-{self.hidden.pk}"')
        response = client.get("/?filtre=avenir")
        self.assertNotContains(response, f'id="date-{self.hidden.pk}"')
        response = client.get(f"/api/periods/{self.period.pk}/live/")
        self.assertNotIn(f"date-{self.hidden.pk}", response.json()["html"])
        response = client.get(f"/api/periods/{self.period.pk}/planning/")
        ids = [e["id"] for e in response.json()["events"]]
        self.assertNotIn(str(self.hidden.pk), ids)

    def test_hidden_detail_404_bureau_ok(self):
        response = Client().get(f"/dates/{self.hidden.pk}/")
        self.assertEqual(response.status_code, 404)
        bureau = _bureau_client()
        response = bureau.get(f"/bureau/dates/{self.hidden.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Masquée")

    def test_hidden_excluded_from_my_registrations(self):
        alice = organizers.get_or_create_organizer("Alice")
        registrations.set_registration(self.hidden.pk, alice.pk, "active", actor=BUREAU)
        visible = self.events[1]
        registrations.set_registration(visible.pk, alice.pk, "active", actor=Actor.public(alice))
        regs_url = f"/api/organizers/{alice.pk}/registrations/?history=1"
        response = Client().get(regs_url)
        event_ids = [r["event_id"] for r in response.json()["registrations"]]
        self.assertNotIn(str(self.hidden.pk), event_ids)
        self.assertIn(str(visible.pk), event_ids)
        response = Client().get(f"/mes-inscriptions/?organizer={alice.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f"/dates/{self.hidden.pk}/")
        self.assertContains(response, f"/dates/{visible.pk}/")

    def test_export_json_contains_is_hidden(self):
        bureau = _bureau_client()
        response = bureau.get("/bureau/exports/donnees.json")
        data = json.loads(response.content)
        flags = {e["id"]: e["is_hidden"] for e in data["events"]}
        self.assertTrue(flags[str(self.hidden.pk)])
        self.assertFalse(flags[str(self.events[1].pk)])

    def test_period_detail_shows_hidden_badge(self):
        bureau = _bureau_client()
        response = bureau.get(f"/bureau/periodes/{self.period.pk}/?staffing=hidden")
        self.assertContains(response, "Masquée")

    def test_bureau_hide_unhide_via_view(self):
        bureau = _bureau_client()
        target = self.events[1]
        response = bureau.post(
            f"/bureau/dates/{target.pk}/",
            {"action": "hide", "expected_version": str(target.version)},
        )
        self.assertEqual(response.status_code, 302)
        target.refresh_from_db()
        self.assertTrue(target.is_hidden)
        self.assertFalse(target.registrations_open)
        response = bureau.post(
            f"/bureau/dates/{target.pk}/",
            {"action": "unhide", "expected_version": str(target.version)},
        )
        target.refresh_from_db()
        self.assertFalse(target.is_hidden)
        self.assertFalse(target.registrations_open)

    def test_bureau_hide_missing_version_invalid(self):
        bureau = _bureau_client()
        target = self.events[1]
        version = target.version
        bureau.post(
            f"/bureau/dates/{target.pk}/",
            {"action": "hide"},
        )
        target.refresh_from_db()
        self.assertFalse(target.is_hidden)
        self.assertEqual(target.version, version)
