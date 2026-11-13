import re
import uuid
from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase

from planning.errors import (
    ArchivedConflict,
    Forbidden,
    ValidationFailed,
    VersionConflict,
)
from planning.models import AuditEntry, Event, Registration
from planning.selectors import bureau_management_events
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


def _signup(event, name):
    organizer = organizers.get_or_create_organizer(name)
    return registrations.set_registration(
        event.pk, organizer.pk, "active", actor=Actor.public(organizer)
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
    free = iter(day for i in range(10) if (day := start + timedelta(days=i)) != first)
    empty = period.events.get(date=first)
    one = Event.objects.create(period=period, date=next(free), min_organizers=2)
    _signup(one, "Seul Inscrit")
    full = Event.objects.create(period=period, date=next(free), min_organizers=2)
    _signup(full, "Prenom Un")
    _signup(full, "Prenom Deux")
    hidden = Event.objects.create(period=period, date=next(free))
    event_services.set_event_hidden(
        hidden.pk, actor=BUREAU, is_hidden=True, expected_version=hidden.version
    )
    cancelled = Event.objects.create(period=period, date=next(free))
    event_services.cancel_event(cancelled.pk, actor=BUREAU)
    past = Event.objects.create(period=period, date=today - timedelta(days=2))
    return {
        "period": period,
        "empty": empty,
        "one": one,
        "full": full,
        "hidden": hidden,
        "cancelled": cancelled,
        "past": past,
    }


@web_client
class ManagementSelectorTests(TestCase):
    def setUp(self):
        self.fx = _fixture()

    def _ids(self, staffing):
        return {event.pk for event in bureau_management_events(self.fx["period"], staffing)}

    def test_filters(self):
        self.assertEqual(
            self._ids("all"),
            {
                self.fx["empty"].pk,
                self.fx["one"].pk,
                self.fx["full"].pk,
                self.fx["hidden"].pk,
            },
        )
        self.assertEqual(self._ids("none"), {self.fx["empty"].pk, self.fx["hidden"].pk})
        self.assertEqual(self._ids("one"), {self.fx["one"].pk})
        self.assertEqual(
            self._ids("under"),
            {self.fx["empty"].pk, self.fx["one"].pk, self.fx["hidden"].pk},
        )
        self.assertEqual(self._ids("hidden"), {self.fx["hidden"].pk})
        self.assertEqual(
            self._ids("history"),
            {
                self.fx["empty"].pk,
                self.fx["one"].pk,
                self.fx["full"].pk,
                self.fx["hidden"].pk,
                self.fx["cancelled"].pk,
                self.fx["past"].pk,
            },
        )

    def test_invalid_filter(self):
        for value in ("0 OR 1=1", "", "bogus"):
            with self.assertRaises(ValidationFailed):
                list(bureau_management_events(self.fx["period"], value))


@web_client
class SimplifiedViewTests(TestCase):
    def setUp(self):
        self.fx = _fixture()
        self.period = self.fx["period"]
        self.url = f"/bureau/periodes/{self.period.pk}/"

    def _post(self, client, **overrides):
        data = {
            "action": "selection",
            "staffing": "all",
            "selection_action": "hide",
            "expected_revision": self.period.revision,
            "event_ids": [str(self.fx["empty"].pk)],
            "confirm": "on",
        }
        data.update(overrides)
        return client.post(self.url, data)

    def test_get_default_all_with_checkboxes(self):
        client = _bureau_client()
        response = client.get(self.url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertContains(response, f'value="{self.fx["empty"].pk}"')
        self.assertContains(response, f'value="{self.fx["full"].pk}"')
        self.assertContains(response, f'value="{self.fx["hidden"].pk}"')
        self.assertNotContains(response, f'value="{self.fx["cancelled"].pk}"')
        self.assertContains(response, "Cochée : visible sur le planning.")
        self.assertContains(response, "bureau-event is-hidden")
        self.assertContains(response, "Masquée")
        self.assertEqual(len(re.findall(r'name="visible"', content)), 4)
        self.assertEqual(len(re.findall(r'name="visible"[^>]* checked', content)), 3)
        self.assertNotContains(response, "Appliquer")
        self.assertNotContains(response, "Tout sélectionner")
        self.assertNotContains(response, 'name="event_ids"')
        self.assertNotContains(response, 'name="selection_action"')
        self.assertIn("no-store", response["Cache-Control"])

    def test_get_filters(self):
        client = _bureau_client()
        response = client.get(self.url + "?staffing=hidden")
        self.assertContains(response, "Masquée")
        self.assertContains(response, f'value="{self.fx["hidden"].pk}"')
        self.assertContains(response, 'name="visible"')
        self.assertNotContains(response, "Appliquer")
        self.assertNotContains(response, "Réafficher les dates")
        self.assertNotContains(response, 'name="event_ids"')
        response = client.get(self.url + "?staffing=all")
        self.assertContains(response, f'value="{self.fx["hidden"].pk}"')
        self.assertContains(response, "bureau-event is-hidden")
        response = client.get(self.url + "?staffing=history")
        self.assertContains(response, f'value="{self.fx["empty"].pk}"')
        self.assertContains(response, "Annulée")
        self.assertContains(response, f'value="{self.fx["cancelled"].pk}"')
        response = client.get(self.url + "?staffing=0 OR 1=1")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="visible"')
        self.assertContains(response, "Aucune date correspondante")

    def test_denied_anonymous_and_staff(self):
        for client in (Client(),):
            self.assertEqual(client.get(self.url).status_code, 302)
            self.assertEqual(client.post(self.url, {}).status_code, 302)
        user = User.objects.create_user(
            username="root", password="x", is_staff=True, is_superuser=True
        )
        client = Client()
        client.force_login(user)
        self.assertEqual(client.get(self.url).status_code, 302)

    def test_hide_selected_only(self):
        client = _bureau_client()
        self.period.refresh_from_db()
        before_regs = list(Registration.objects.order_by("pk").values())
        response = self._post(client, staffing="none")
        self.assertEqual(response.status_code, 302)
        empty = Event.objects.get(pk=self.fx["empty"].pk)
        self.assertTrue(empty.is_hidden)
        keep = Event.objects.get(pk=self.fx["one"].pk)
        self.assertFalse(keep.is_hidden)
        self.assertTrue(keep.registrations_open)
        self.assertEqual(list(Registration.objects.order_by("pk").values()), before_regs)

    def test_post_close_action_rejected(self):
        client = _bureau_client()
        self.period.refresh_from_db()
        response = self._post(
            client,
            staffing="one",
            selection_action="close",
            event_ids=[str(self.fx["one"].pk)],
        )
        self.assertEqual(response.status_code, 200)
        one = Event.objects.get(pk=self.fx["one"].pk)
        self.assertTrue(one.registrations_open)
        self.assertFalse(one.is_hidden)

    def test_close_service_stale_revision(self):
        self.period.refresh_from_db()
        stale = self.period.revision
        _signup(self.fx["empty"], "Concurrent")
        with self.assertRaises(VersionConflict):
            period_services.close_selected_dates(
                self.period.pk,
                actor=BUREAU,
                event_ids=[self.fx["empty"].pk],
                staffing_filter="none",
                expected_revision=stale,
            )
        self.assertTrue(Event.objects.get(pk=self.fx["empty"].pk).registrations_open)

    def test_close_service_ineligible_id(self):
        self.period.refresh_from_db()
        with self.assertRaises(ValidationFailed):
            period_services.close_selected_dates(
                self.period.pk,
                actor=BUREAU,
                event_ids=[self.fx["full"].pk],
                staffing_filter="none",
                expected_revision=self.period.revision,
            )

    def test_close_service_two_selected_revision_once(self):
        period = self.period
        period.refresh_from_db()
        before_rev = period.revision
        before_regs = list(Registration.objects.order_by("pk").values())
        empty_v = self.fx["empty"].version
        one_v = self.fx["one"].version
        full_v = self.fx["full"].version
        changed = period_services.close_selected_dates(
            period.pk,
            actor=BUREAU,
            event_ids=[self.fx["empty"].pk, self.fx["one"].pk],
            staffing_filter="all",
            expected_revision=before_rev,
        )
        self.assertEqual(set(changed), {str(self.fx["empty"].pk), str(self.fx["one"].pk)})
        for event_id, version in (
            (self.fx["empty"].pk, empty_v),
            (self.fx["one"].pk, one_v),
        ):
            event = Event.objects.get(pk=event_id)
            self.assertFalse(event.registrations_open)
            self.assertFalse(event.is_hidden)
            self.assertEqual(event.version, version + 1)
        period.refresh_from_db()
        self.assertEqual(period.revision, before_rev + 1)
        full = Event.objects.get(pk=self.fx["full"].pk)
        self.assertTrue(full.registrations_open)
        self.assertEqual(full.version, full_v)
        self.assertEqual(list(Registration.objects.order_by("pk").values()), before_regs)

    def test_close_service_noop_already_closed(self):
        period = self.period
        period.refresh_from_db()
        closed = period_services.close_selected_dates(
            period.pk,
            actor=BUREAU,
            event_ids=[self.fx["one"].pk],
            staffing_filter="one",
            expected_revision=period.revision,
        )
        self.assertEqual(closed, [str(self.fx["one"].pk)])
        period.refresh_from_db()
        revision = period.revision
        version = Event.objects.get(pk=self.fx["one"].pk).version
        audits = AuditEntry.objects.filter(target_id=str(self.fx["one"].pk)).count()
        closed = period_services.close_selected_dates(
            period.pk,
            actor=BUREAU,
            event_ids=[self.fx["one"].pk],
            staffing_filter="one",
            expected_revision=period.revision,
        )
        self.assertEqual(closed, [])
        period.refresh_from_db()
        self.assertEqual(period.revision, revision)
        self.assertEqual(Event.objects.get(pk=self.fx["one"].pk).version, version)
        self.assertEqual(
            AuditEntry.objects.filter(target_id=str(self.fx["one"].pk)).count(), audits
        )

    def test_close_service_rejects_invalid_contexts(self):
        period = self.period
        period.refresh_from_db()
        other = period_services.create_period(
            actor=BUREAU,
            label="S2",
            start_date=date.today() + timedelta(days=30),
            end_date=date.today() + timedelta(days=40),
            creation_key=uuid.uuid4(),
        )
        foreign = other.events.order_by("date").first()
        with self.assertRaises(ValidationFailed):
            period_services.close_selected_dates(
                period.pk,
                actor=BUREAU,
                event_ids=[foreign.pk],
                staffing_filter="all",
                expected_revision=period.revision,
            )
        with self.assertRaises(ValidationFailed):
            period_services.close_selected_dates(
                period.pk,
                actor=BUREAU,
                event_ids=[self.fx["empty"].pk],
                staffing_filter="all",
                expected_revision=True,
            )
        organizer = organizers.get_or_create_organizer("Acteur Public")
        with self.assertRaises(Forbidden):
            period_services.close_selected_dates(
                period.pk,
                actor=Actor.public(organizer),
                event_ids=[self.fx["empty"].pk],
                staffing_filter="all",
                expected_revision=period.revision,
            )
        period_services.archive(period.pk, actor=BUREAU)
        period.refresh_from_db()
        with self.assertRaises(ArchivedConflict):
            period_services.close_selected_dates(
                period.pk,
                actor=BUREAU,
                event_ids=[self.fx["empty"].pk],
                staffing_filter="all",
                expected_revision=period.revision,
            )
        self.assertTrue(Event.objects.get(pk=self.fx["empty"].pk).registrations_open)

    def test_unhide_service_two_selected(self):
        period = self.period
        keep_hidden = Event.objects.create(period=period, date=period.end_date)
        event_services.set_event_hidden(
            keep_hidden.pk,
            actor=BUREAU,
            is_hidden=True,
            expected_version=keep_hidden.version,
        )
        full = self.fx["full"]
        full.refresh_from_db()
        event_services.set_event_hidden(
            full.pk,
            actor=BUREAU,
            is_hidden=True,
            expected_version=full.version,
        )
        period.refresh_from_db()
        before_rev = period.revision
        before_regs = list(Registration.objects.order_by("pk").values())
        hidden_v = Event.objects.get(pk=self.fx["hidden"].pk).version
        full_v = Event.objects.get(pk=self.fx["full"].pk).version
        changed = period_services.unhide_selected_dates(
            period.pk,
            actor=BUREAU,
            event_ids=[self.fx["hidden"].pk, self.fx["full"].pk],
            expected_revision=before_rev,
        )
        self.assertEqual({e.pk for e in changed}, {self.fx["hidden"].pk, self.fx["full"].pk})
        for event_id, version in (
            (self.fx["hidden"].pk, hidden_v),
            (self.fx["full"].pk, full_v),
        ):
            event = Event.objects.get(pk=event_id)
            self.assertFalse(event.is_hidden)
            self.assertFalse(event.registrations_open)
            self.assertEqual(event.version, version + 1)
        period.refresh_from_db()
        self.assertEqual(period.revision, before_rev + 1)
        keep_hidden.refresh_from_db()
        self.assertTrue(keep_hidden.is_hidden)
        self.assertEqual(list(Registration.objects.order_by("pk").values()), before_regs)
        audits = AuditEntry.objects.filter(action="event.unhide").count()
        self.assertEqual(audits, 2)

    def test_unhide_service_cancelled_status_preserved(self):
        cancelled = self.fx["cancelled"]
        cancelled.refresh_from_db()
        event_services.set_event_hidden(
            cancelled.pk,
            actor=BUREAU,
            is_hidden=True,
            expected_version=cancelled.version,
        )
        self.period.refresh_from_db()
        period_services.unhide_selected_dates(
            self.period.pk,
            actor=BUREAU,
            event_ids=[cancelled.pk],
            expected_revision=self.period.revision,
        )
        cancelled.refresh_from_db()
        self.assertFalse(cancelled.is_hidden)
        self.assertEqual(cancelled.status, "cancelled")

    def test_unhide_service_rejections(self):
        period = self.period
        period.refresh_from_db()
        stale = period.revision
        _signup(self.fx["one"], "Concurrent")
        cases = [
            {
                "event_ids": [self.fx["hidden"].pk],
                "expected_revision": stale,
                "raises": VersionConflict,
            },
            {"event_ids": [], "raises": ValidationFailed},
            {"event_ids": "abc", "raises": ValidationFailed},
            {"event_ids": ["0 OR 1=1"], "raises": ValidationFailed},
            {"event_ids": [self.fx["one"].pk], "raises": ValidationFailed},
            {"event_ids": [self.fx["hidden"].pk, self.fx["one"].pk], "raises": ValidationFailed},
            {"event_ids": [str(uuid.uuid4())], "raises": ValidationFailed},
            {
                "event_ids": [self.fx["hidden"].pk],
                "expected_revision": True,
                "raises": ValidationFailed,
            },
            {
                "event_ids": [self.fx["hidden"].pk],
                "expected_revision": "0 OR 1=1",
                "raises": ValidationFailed,
            },
        ]
        for kwargs in cases:
            period.refresh_from_db()
            expected = kwargs.pop("raises")
            kwargs.setdefault("expected_revision", period.revision)
            with self.assertRaises(expected, msg=str(kwargs)):
                period_services.unhide_selected_dates(period.pk, actor=BUREAU, **kwargs)
            self.assertTrue(Event.objects.get(pk=self.fx["hidden"].pk).is_hidden)
        organizer = organizers.get_or_create_organizer("Acteur Public")
        with self.assertRaises(Forbidden):
            period_services.unhide_selected_dates(
                period.pk,
                actor=Actor.public(organizer),
                event_ids=[self.fx["hidden"].pk],
                expected_revision=period.revision,
            )
        period_services.archive(period.pk, actor=BUREAU)
        period.refresh_from_db()
        with self.assertRaises(ArchivedConflict):
            period_services.unhide_selected_dates(
                period.pk,
                actor=BUREAU,
                event_ids=[self.fx["hidden"].pk],
                expected_revision=period.revision,
            )

    def test_post_invalid_variants_no_mutation(self):
        client = _bureau_client()
        self.period.refresh_from_db()
        cases = [
            {"event_ids": []},
            {"selection_action": ""},
            {"confirm": ""},
            {"expected_revision": "0 OR 1=1"},
            {"event_ids": ["0 OR 1=1"]},
            {"event_ids": [str(self.fx["hidden"].pk)]},
            {"event_ids": [str(self.fx["past"].pk)]},
            {"event_ids": [str(self.fx["cancelled"].pk)]},
            {"event_ids": [str(uuid.uuid4())]},
            {"staffing": "bogus"},
            {"staffing": "none", "event_ids": [str(self.fx["one"].pk)]},
            {
                "staffing": "hidden",
                "selection_action": "hide",
                "event_ids": [str(self.fx["hidden"].pk)],
            },
            {
                "staffing": "hidden",
                "selection_action": "close",
                "event_ids": [str(self.fx["hidden"].pk)],
            },
            {
                "staffing": "all",
                "selection_action": "unhide",
                "event_ids": [str(self.fx["one"].pk)],
            },
        ]
        for overrides in cases:
            self.period.refresh_from_db()
            overrides.setdefault("expected_revision", self.period.revision)
            response = self._post(client, **overrides)
            self.assertIn(response.status_code, (200, 302), overrides)
            self.assertEqual(Event.objects.filter(period=self.period, is_hidden=True).count(), 1)
            self.assertTrue(Event.objects.get(pk=self.fx["empty"].pk).registrations_open)

    def test_post_stale_after_selected_signup(self):
        client = _bureau_client()
        self.period.refresh_from_db()
        stale = self.period.revision
        _signup(self.fx["empty"], "Concurrent Sur Date")
        response = self._post(client, staffing="none", expected_revision=stale)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Event.objects.get(pk=self.fx["empty"].pk).is_hidden)
        self.assertContains(response, 'class="errorlist"', html=False)

    def test_post_unhide_valid_restores_closed(self):
        client = _bureau_client()
        self.period.refresh_from_db()
        before_regs = list(Registration.objects.order_by("pk").values())
        response = self._post(
            client,
            staffing="hidden",
            selection_action="unhide",
            event_ids=[str(self.fx["hidden"].pk)],
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("staffing=hidden", response["Location"])
        hidden = Event.objects.get(pk=self.fx["hidden"].pk)
        self.assertFalse(hidden.is_hidden)
        self.assertFalse(hidden.registrations_open)
        self.assertEqual(list(Registration.objects.order_by("pk").values()), before_regs)

    def test_post_unhide_missing_confirm_no_mutation(self):
        client = _bureau_client()
        self.period.refresh_from_db()
        response = self._post(
            client,
            staffing="hidden",
            selection_action="unhide",
            event_ids=[str(self.fx["hidden"].pk)],
            confirm="",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Event.objects.get(pk=self.fx["hidden"].pk).is_hidden)

    def test_archived_read_only(self):
        period_services.archive(self.period.pk, actor=BUREAU)
        client = _bureau_client()
        response = client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Appliquer")
        self.assertNotContains(response, "event_ids")
        content = response.content.decode()
        self.assertEqual(
            len(re.findall(r'name="visible"[^>]*disabled', content)),
            len(re.findall(r'name="visible"', content)),
        )
        self.period.refresh_from_db()
        response = self._post(client)
        self.assertFalse(Event.objects.get(pk=self.fx["empty"].pk).is_hidden)

    def test_legacy_period_actions_still_work(self):
        client = _bureau_client()
        today = date.today()
        other = period_services.create_period(
            actor=BUREAU,
            label="S2",
            start_date=today + timedelta(days=30),
            end_date=today + timedelta(days=40),
            creation_key=uuid.uuid4(),
        )
        response = client.post(
            f"/bureau/periodes/{other.pk}/",
            {
                "action": "update",
                "label": "S2 renommée",
                "start_date": other.start_date.isoformat(),
                "end_date": other.end_date.isoformat(),
                "expected_version": other.version,
            },
        )
        self.assertEqual(response.status_code, 302)
        other.refresh_from_db()
        self.assertEqual(other.label, "S2 renommée")
        response = client.post(self.url, {"action": "archive"})
        self.assertEqual(response.status_code, 302)
        self.period.refresh_from_db()
        self.assertTrue(self.period.is_archived)
        response = client.post(self.url, {"action": "reopen"})
        self.assertEqual(response.status_code, 302)
        self.period.refresh_from_db()
        self.assertFalse(self.period.is_archived)

    def test_options_collapsed_with_legacy_controls(self):
        client = _bureau_client()
        response = client.get(self.url)
        content = response.content.decode()
        self.assertIn("<summary>Options</summary>", content)
        self.assertNotIn('<details class="card" open>', content)
        self.assertNotIn("Clôturer les inscriptions", content)
        self.assertNotIn("Ouvrir les inscriptions", content)
        self.assertNotIn("Fermer les inscriptions", content)
        self.assertIn("Export CSV", content)
        self.assertIn("generate_missing", content)
        self.assertIn('name="action" value="update"', content)

    def test_dashboard_simplified(self):
        client = _bureau_client()
        response = client.get("/bureau/")
        content = response.content.decode()
        self.assertIn("<summary>Options</summary>", content)
        self.assertIn("donnees.json", content)
        self.assertNotIn("Export CSV", content)
        self.assertIn("Gérer", content)
        self.assertIn("Créer une période", content)
