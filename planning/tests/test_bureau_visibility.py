import json
import uuid
from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase

from planning.errors import (
    ArchivedConflict,
    Forbidden,
    NotFound,
    ValidationFailed,
    VersionConflict,
)
from planning.models import AuditEntry, Event, Registration
from planning.selectors import bureau_management_events, filtered_events
from planning.services import organizers, registrations
from planning.services import periods as period_services
from planning.services.actors import Actor
from planning.tests import web_client

BUREAU = Actor.bureau()


def _bureau_client(**kwargs):
    group, _ = Group.objects.get_or_create(name="Bureau")
    user = User.objects.create_user(username="bureau", password="x")
    user.groups.add(group)
    client = Client(**kwargs)
    client.force_login(user)
    return client


def _signup(event, name, state="active", note=None):
    organizer = organizers.get_or_create_organizer(name)
    return registrations.set_registration(
        event.pk, organizer.pk, state, actor=Actor.public(organizer), note=note
    )


def _fixture():
    today = date.today()
    start = today + timedelta(days=1)
    end = today + timedelta(days=10)
    first = start + timedelta(days=(5 - start.weekday()) % 7)
    period = period_services.create_period(
        actor=BUREAU,
        label="S1",
        start_date=start,
        end_date=end,
        creation_key=uuid.uuid4(),
        selected_dates=[first],
    )
    visible = period.events.get(date=first)
    _signup(visible, "Prenom Un")
    _signup(visible, "Retire Deux", state="withdrawn", note="indispo")
    hidden = Event.objects.create(period=period, date=start + timedelta(days=8))
    period_services.set_date_visibility(
        period.pk,
        hidden.pk,
        actor=BUREAU,
        is_visible=False,
        expected_revision=_fresh_revision(period),
        expected_version=hidden.version,
    )
    return {"period": period, "visible": visible, "hidden": hidden}


def _fresh_revision(period):
    period.refresh_from_db()
    return period.revision


def _regs_snapshot():
    return list(Registration.objects.order_by("pk").values())


@web_client
class VisibilityServiceTests(TestCase):
    def setUp(self):
        self.fx = _fixture()
        self.period = self.fx["period"]

    def _call(self, event, is_visible, **overrides):
        kwargs = {
            "actor": BUREAU,
            "is_visible": is_visible,
            "expected_revision": _fresh_revision(self.period),
            "expected_version": Event.objects.get(pk=event.pk).version,
        }
        kwargs.update(overrides)
        return period_services.set_date_visibility(self.period.pk, event.pk, **kwargs)

    def test_hide_closes_and_unhide_keeps_closed(self):
        event = self.fx["visible"]
        before_regs = _regs_snapshot()
        revision = _fresh_revision(self.period)
        version = Event.objects.get(pk=event.pk).version
        audits = AuditEntry.objects.count()

        result = self._call(event, is_visible=False)
        self.assertTrue(result.is_hidden)
        self.assertFalse(result.registrations_open)
        event.refresh_from_db()
        self.assertTrue(event.is_hidden)
        self.assertEqual(event.version, version + 1)
        self.assertEqual(_fresh_revision(self.period), revision + 1)
        self.assertEqual(_regs_snapshot(), before_regs)
        self.assertEqual(AuditEntry.objects.filter(action="event.hide").count(), 2)

        result = self._call(event, is_visible=True)
        self.assertFalse(result.is_hidden)
        self.assertFalse(result.registrations_open)
        event.refresh_from_db()
        self.assertFalse(event.is_hidden)
        self.assertFalse(event.registrations_open)
        self.assertEqual(event.version, version + 2)
        self.assertEqual(_fresh_revision(self.period), revision + 2)
        self.assertEqual(_regs_snapshot(), before_regs)
        self.assertEqual(AuditEntry.objects.count(), audits + 2)

    def test_noop_unhide_visible_open_preserves(self):
        event = self.fx["visible"]
        event.registrations_open = True
        event.save(update_fields=["registrations_open", "updated_at"])
        revision = _fresh_revision(self.period)
        version = Event.objects.get(pk=event.pk).version
        audits = AuditEntry.objects.count()
        result = self._call(event, is_visible=True)
        self.assertFalse(result.is_hidden)
        self.assertTrue(result.registrations_open)
        event.refresh_from_db()
        self.assertEqual(event.version, version)
        self.assertEqual(_fresh_revision(self.period), revision)
        self.assertEqual(AuditEntry.objects.count(), audits)

    def test_stale_revision_after_join_rejected(self):
        event = self.fx["visible"]
        stale = _fresh_revision(self.period)
        _signup(event, "Nouveau Venu")
        with self.assertRaises(VersionConflict):
            self._call(event, is_visible=False, expected_revision=stale)
        event.refresh_from_db()
        self.assertFalse(event.is_hidden)

    def test_stale_event_version_rejected(self):
        event = self.fx["visible"]
        with self.assertRaises(VersionConflict):
            self._call(
                event,
                is_visible=False,
                expected_version=event.version + 10,
            )
        event.refresh_from_db()
        self.assertFalse(event.is_hidden)

    def test_rejections(self):
        event = self.fx["visible"]
        foreign = Event.objects.create(
            period=period_services.create_period(
                actor=BUREAU,
                label="Autre",
                start_date=date.today() + timedelta(days=30),
                end_date=date.today() + timedelta(days=40),
                creation_key=uuid.uuid4(),
            ),
            date=date.today() + timedelta(days=33),
        )
        cases = [
            {"event": foreign, "raises": NotFound},
            {"event": event, "expected_revision": True, "raises": ValidationFailed},
            {
                "event": event,
                "expected_revision": "abc' OR 1=1",
                "raises": ValidationFailed,
            },
            {"event": event, "expected_version": True, "raises": ValidationFailed},
            {"event": event, "is_visible": "oui", "raises": ValidationFailed},
            {
                "event": event,
                "actor": Actor.public(organizers.get_or_create_organizer("Public")),
                "raises": Forbidden,
            },
        ]
        for case in cases:
            raises = case.pop("raises")
            target = case.pop("event")
            with self.assertRaises(raises, msg=str(case)):
                period_services.set_date_visibility(
                    self.period.pk,
                    target.pk,
                    **{
                        "actor": BUREAU,
                        "is_visible": False,
                        "expected_revision": _fresh_revision(self.period),
                        "expected_version": 1,
                        **case,
                    },
                )
        event.refresh_from_db()
        self.assertFalse(event.is_hidden)

    def test_missing_event_404(self):
        with self.assertRaises(NotFound):
            period_services.set_date_visibility(
                self.period.pk,
                uuid.uuid4(),
                actor=BUREAU,
                is_visible=False,
                expected_revision=_fresh_revision(self.period),
                expected_version=1,
            )

    def test_archived_rejected(self):
        period_services.archive(self.period.pk, actor=BUREAU)
        with self.assertRaises(ArchivedConflict):
            self._call(self.fx["visible"], is_visible=False)
        self.assertFalse(Event.objects.get(pk=self.fx["visible"].pk).is_hidden)

    def test_management_filters_include_hidden_public_excludes(self):
        ids = {event.pk for event in bureau_management_events(self.period, "all")}
        self.assertIn(self.fx["hidden"].pk, ids)
        none_ids = {event.pk for event in bureau_management_events(self.period, "none")}
        self.assertIn(self.fx["hidden"].pk, none_ids)
        public_ids = {event.pk for event in filtered_events(self.period, "avenir")[0]}
        self.assertNotIn(self.fx["hidden"].pk, public_ids)
        self.assertIn(self.fx["visible"].pk, public_ids)


@web_client
class VisibilityViewTests(TestCase):
    def setUp(self):
        self.fx = _fixture()
        self.period = self.fx["period"]
        self.url = f"/bureau/periodes/{self.period.pk}/"

    def _data(self, event, visible="on", **overrides):
        data = {
            "action": "visibility",
            "event_id": str(event.pk),
            "visible": visible,
            "staffing": "all",
            "expected_revision": _fresh_revision(self.period),
            "expected_version": Event.objects.get(pk=event.pk).version,
        }
        if visible is None:
            data.pop("visible")
        data.update(overrides)
        return data

    def test_post_ordinary_redirects_and_hides(self):
        client = _bureau_client()
        event = self.fx["visible"]
        response = client.post(self.url, self._data(event, visible=None))
        self.assertEqual(response.status_code, 302)
        self.assertIn("staffing=all", response["Location"])
        event.refresh_from_db()
        self.assertTrue(event.is_hidden)
        self.assertFalse(event.registrations_open)

    def test_post_ajax_json_payload(self):
        client = _bureau_client()
        event = self.fx["visible"]
        response = client.post(
            self.url,
            self._data(event, visible=None),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertTrue(body["event"]["is_hidden"])
        self.assertFalse(body["event"]["registrations_open"])
        self.assertEqual(body["event"]["id"], str(event.pk))
        self.assertEqual(body["revision"], _fresh_revision(self.period))
        event.refresh_from_db()
        self.assertTrue(event.is_hidden)

        response = client.post(
            self.url,
            self._data(event),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertFalse(body["event"]["is_hidden"])
        self.assertTrue(body["event"]["registrations_open"])

    def test_ajax_invalid_fields_400(self):
        client = _bureau_client()
        for data in (
            self._data(self.fx["visible"], event_id="abc' OR 1=1"),
            self._data(self.fx["visible"], expected_revision="x"),
            self._data(self.fx["visible"], expected_version=""),
        ):
            response = client.post(self.url, data, HTTP_ACCEPT="application/json")
            self.assertEqual(response.status_code, 400)
            self.assertEqual(json.loads(response.content)["error"]["code"], "invalid")
        self.assertFalse(Event.objects.get(pk=self.fx["visible"].pk).is_hidden)

    def test_ajax_foreign_event_404_no_leak(self):
        client = _bureau_client()
        foreign = Event.objects.create(
            period=period_services.create_period(
                actor=BUREAU,
                label="Autre",
                start_date=date.today() + timedelta(days=30),
                end_date=date.today() + timedelta(days=40),
                creation_key=uuid.uuid4(),
            ),
            date=date.today() + timedelta(days=33),
        )
        response = client.post(
            self.url,
            self._data(self.fx["visible"], event_id=str(foreign.pk)),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 404)
        body = json.loads(response.content)
        self.assertNotIn("event", body)
        self.assertNotIn(str(foreign.pk), response.content.decode())

    def test_two_requests_same_revision_second_409(self):
        client = _bureau_client()
        event = self.fx["visible"]
        stale = _fresh_revision(self.period)
        first = client.post(
            self.url,
            self._data(event, visible=None, expected_revision=stale),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(first.status_code, 200)
        second = client.post(
            self.url,
            self._data(event, visible=None, expected_revision=stale),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(second.status_code, 409)
        body = json.loads(second.content)
        self.assertTrue(body["event"]["is_hidden"])
        self.assertEqual(body["revision"], _fresh_revision(self.period))
        self.assertTrue(Event.objects.get(pk=event.pk).is_hidden)

    def test_csrf_403_then_valid_succeeds(self):
        client = _bureau_client(enforce_csrf_checks=True)
        event = self.fx["visible"]
        response = client.post(self.url, self._data(event, visible=None))
        self.assertEqual(response.status_code, 403)
        client.get(self.url)
        token = client.cookies["csrftoken"].value
        data = self._data(event, visible=None)
        data["csrfmiddlewaretoken"] = token
        response = client.post(
            self.url,
            data,
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Event.objects.get(pk=event.pk).is_hidden)

    def test_get_checkbox_reflects_state_and_names_escaped(self):
        _signup(self.fx["visible"], "Seul <Nom>")
        client = _bureau_client()
        response = client.get(self.url)
        content = response.content.decode()
        self.assertIn("Cochée : visible sur le planning.", content)
        self.assertIn("bureau-event is-hidden", content)
        self.assertIn("Masquée", content)
        self.assertIn("Seul &lt;Nom&gt;", content)
        self.assertNotIn("Seul <Nom>", content)
        self.assertNotIn('name="event_ids"', content)
        self.assertNotIn("Appliquer", content)
