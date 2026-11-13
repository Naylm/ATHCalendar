import uuid
from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.utils.html import escape

from planning.errors import (
    ArchivedConflict,
    Forbidden,
    ValidationFailed,
    VersionConflict,
)
from planning.models import AuditEntry, Event, Registration
from planning.selectors import bureau_cleanup_events
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
    empty = period.events.get(date=first)
    _signup(empty, "Fantome Retire")
    Registration.objects.filter(event=empty).update(state="withdrawn")
    free = iter(day for i in range(10) if (day := start + timedelta(days=i)) != first)
    one = Event.objects.create(period=period, date=next(free), min_organizers=2)
    _signup(one, "Seul Inscrit")
    full = Event.objects.create(period=period, date=next(free), min_organizers=2)
    _signup(full, "Prenom Un")
    _signup(full, "Prenom Deux")
    under = Event.objects.create(period=period, date=next(free), min_organizers=3)
    _signup(under, "Manque Un")
    _signup(under, "Manque Deux")
    hidden = Event.objects.create(period=period, date=next(free))
    event_services.set_event_hidden(
        hidden.pk, actor=BUREAU, is_hidden=True, expected_version=hidden.version
    )
    cancelled = Event.objects.create(period=period, date=next(free))
    event_services.cancel_event(cancelled.pk, actor=BUREAU)
    past = Event.objects.create(period=period, date=today - timedelta(days=2))
    other_period = period_services.create_period(
        actor=BUREAU,
        label="S2",
        start_date=end + timedelta(days=30),
        end_date=end + timedelta(days=45),
        creation_key=uuid.uuid4(),
    )
    foreign = other_period.events.order_by("date").first()
    return {
        "period": period,
        "other": other_period,
        "empty": empty,
        "one": one,
        "full": full,
        "under": under,
        "hidden": hidden,
        "cancelled": cancelled,
        "past": past,
        "foreign": foreign,
    }


def _cleanup(ids, period, staffing="one", revision=None, actor=BUREAU):
    period.refresh_from_db()
    return period_services.hide_selected_dates(
        period.pk,
        actor=actor,
        event_ids=ids,
        staffing_filter=staffing,
        expected_revision=period.revision if revision is None else revision,
    )


@web_client
class CleanupSelectorTests(TestCase):
    def setUp(self):
        self.fx = _fixture()

    def _ids(self, staffing):
        return {event.pk for event in bureau_cleanup_events(self.fx["period"], staffing)}

    def test_filter_none_only_empty(self):
        self.assertEqual(self._ids("none"), {self.fx["empty"].pk})

    def test_filter_one_counts_active_only(self):
        self.assertEqual(self._ids("one"), {self.fx["one"].pk})

    def test_filter_under_includes_below_minimum(self):
        self.assertEqual(
            self._ids("under"),
            {self.fx["empty"].pk, self.fx["one"].pk, self.fx["under"].pk},
        )

    def test_filter_all_future_visible_scheduled(self):
        self.assertEqual(
            self._ids("all"),
            {
                self.fx["empty"].pk,
                self.fx["one"].pk,
                self.fx["full"].pk,
                self.fx["under"].pk,
            },
        )

    def test_invalid_filter_rejected(self):
        for value in ("0 OR 1=1", "", "none;DROP", "2"):
            with self.assertRaises(ValidationFailed):
                list(bureau_cleanup_events(self.fx["period"], value))


@web_client
class CleanupServiceTests(TestCase):
    def setUp(self):
        self.fx = _fixture()
        self.period = self.fx["period"]

    def test_hides_selected_only_and_restores(self):
        period = self.period
        period.refresh_from_db()
        before_regs = list(Registration.objects.order_by("pk").values())
        before_rev = period.revision
        keep = self.fx["one"]
        before_version = keep.version
        empty_before_version = self.fx["empty"].version
        changed = _cleanup([self.fx["empty"].pk], period, staffing="none")
        self.assertEqual([event.pk for event in changed], [self.fx["empty"].pk])
        empty = Event.objects.get(pk=self.fx["empty"].pk)
        self.assertTrue(empty.is_hidden)
        self.assertFalse(empty.registrations_open)
        self.assertEqual(empty.version, empty_before_version + 1)
        period.refresh_from_db()
        self.assertEqual(period.revision, before_rev + 1)
        keep.refresh_from_db()
        self.assertFalse(keep.is_hidden)
        self.assertTrue(keep.registrations_open)
        self.assertEqual(keep.version, before_version)
        self.assertEqual(list(Registration.objects.order_by("pk").values()), before_regs)
        audits = AuditEntry.objects.filter(action="event.hide", target_id=str(empty.pk))
        self.assertEqual(audits.count(), 1)
        self.assertEqual(audits.get().before["is_hidden"], "False")
        self.assertEqual(audits.get().after["is_hidden"], "True")
        reg = Registration.objects.filter(event=empty).first()
        event_services.set_event_hidden(
            empty.pk, actor=BUREAU, is_hidden=False, expected_version=empty.version
        )
        empty.refresh_from_db()
        self.assertFalse(empty.is_hidden)
        self.assertEqual(reg.state, "withdrawn")
        one_regs = Registration.objects.filter(event=keep, state="active")
        self.assertEqual(one_regs.count(), 1)

    def test_multiple_selected_hidden_one_operation(self):
        period = self.period
        extra = event_services.create_event(
            period.pk,
            actor=BUREAU,
            date_value=period.end_date,
            min_organizers=2,
        )
        period.refresh_from_db()
        before_rev = period.revision
        before_regs = list(Registration.objects.order_by("pk").values())
        empty_v = self.fx["empty"].version
        extra_v = extra.version
        keep = self.fx["one"]
        keep_v = keep.version
        changed = _cleanup([self.fx["empty"].pk, extra.pk], period, staffing="none")
        self.assertEqual({event.pk for event in changed}, {self.fx["empty"].pk, extra.pk})
        empty = Event.objects.get(pk=self.fx["empty"].pk)
        extra = Event.objects.get(pk=extra.pk)
        for event, version in ((empty, empty_v), (extra, extra_v)):
            self.assertTrue(event.is_hidden)
            self.assertFalse(event.registrations_open)
            self.assertEqual(event.version, version + 1)
        period.refresh_from_db()
        self.assertEqual(period.revision, before_rev + 1)
        keep.refresh_from_db()
        self.assertFalse(keep.is_hidden)
        self.assertTrue(keep.registrations_open)
        self.assertEqual(keep.version, keep_v)
        self.assertEqual(list(Registration.objects.order_by("pk").values()), before_regs)

    def test_signup_on_selected_rejects_stale_and_filters_choice(self):
        period = self.period
        period.refresh_from_db()
        stale = period.revision
        _signup(self.fx["empty"], "Concurrent Sur Date")
        with self.assertRaises(VersionConflict):
            _cleanup([self.fx["empty"].pk], period, staffing="none", revision=stale)
        self.assertFalse(Event.objects.get(pk=self.fx["empty"].pk).is_hidden)
        client = _bureau_client()
        response = client.post(
            f"/bureau/periodes/{period.pk}/nettoyage/",
            {
                "staffing": "none",
                "expected_revision": stale,
                "event_ids": [str(self.fx["empty"].pk)],
                "confirm_hide": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Event.objects.get(pk=self.fx["empty"].pk).is_hidden)
        self.assertContains(response, 'class="errorlist"', html=False)
        self.assertContains(response, "Aucune date correspondante")

    def test_selected_event_version_bumped_once(self):
        empty = self.fx["empty"]
        before = empty.version
        _cleanup([empty.pk], self.period, staffing="none")
        empty.refresh_from_db()
        self.assertEqual(empty.version, before + 1)

    def test_stale_revision_rejects_all(self):
        revision = self.period.revision
        _signup(self.fx["one"], "Concurrent Nouveau")
        with self.assertRaises(VersionConflict):
            _cleanup([self.fx["empty"].pk], self.period, staffing="none", revision=revision)
        self.assertFalse(Event.objects.get(pk=self.fx["empty"].pk).is_hidden)

    def test_ineligible_ids_rejected_no_mutation(self):
        bad_sets = [
            [self.fx["foreign"].pk],
            [self.fx["hidden"].pk],
            [self.fx["cancelled"].pk],
            [self.fx["past"].pk],
            [self.fx["full"].pk],
            [self.fx["empty"].pk, self.fx["one"].pk],
            [uuid.uuid4()],
        ]
        for ids in bad_sets:
            with self.assertRaises(ValidationFailed, msg=str(ids)):
                _cleanup(ids, self.period, staffing="none")
            self.assertEqual(Event.objects.filter(period=self.period, is_hidden=True).count(), 1)

    def test_malformed_arguments_rejected(self):
        cases = [
            {"event_ids": [], "staffing_filter": "none"},
            {"event_ids": ["0 OR 1=1"], "staffing_filter": "none"},
            {"event_ids": "abc", "staffing_filter": "none"},
            {"event_ids": [self.fx["empty"].pk], "staffing_filter": "0 OR 1=1"},
            {
                "event_ids": [self.fx["empty"].pk],
                "staffing_filter": "none",
                "expected_revision": True,
            },
            {
                "event_ids": [self.fx["empty"].pk],
                "staffing_filter": "none",
                "expected_revision": "0 OR 1=1",
            },
        ]
        for kwargs in cases:
            self.period.refresh_from_db()
            call = {"expected_revision": self.period.revision, **kwargs}
            with self.assertRaises((ValidationFailed, VersionConflict), msg=str(kwargs)):
                period_services.hide_selected_dates(self.period.pk, actor=BUREAU, **call)
            self.assertEqual(Event.objects.filter(period=self.period, is_hidden=True).count(), 1)

    def test_over_batch_limit_rejected(self):
        ids = [str(uuid.uuid4()) for _ in range(201)]
        with self.assertRaises(ValidationFailed):
            _cleanup(ids, self.period, staffing="all")

    def test_public_actor_forbidden(self):
        organizer = organizers.get_or_create_organizer("Public Actor")
        with self.assertRaises(Forbidden):
            _cleanup(
                [self.fx["empty"].pk], self.period, staffing="none", actor=Actor.public(organizer)
            )

    def test_archived_period_rejected(self):
        period_services.archive(self.period.pk, actor=BUREAU)
        with self.assertRaises(ArchivedConflict):
            _cleanup([self.fx["empty"].pk], self.period, staffing="none")


@web_client
class CleanupViewTests(TestCase):
    def setUp(self):
        self.fx = _fixture()
        self.period = self.fx["period"]
        self.url = f"/bureau/periodes/{self.period.pk}/nettoyage/"

    def test_anonymous_and_non_bureau_denied(self):
        for client in (Client(),):
            self.assertEqual(client.get(self.url).status_code, 302)
            self.assertEqual(client.post(self.url, {}).status_code, 302)
        user = User.objects.create_user(
            username="root", password="x", is_staff=True, is_superuser=True
        )
        client = Client()
        client.force_login(user)
        self.assertEqual(client.get(self.url).status_code, 302)

    def test_get_default_zero_filter(self):
        client = _bureau_client()
        response = client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Trier les dates")
        self.assertContains(response, f'value="{self.fx["empty"].pk}"')
        self.assertNotContains(response, f'value="{self.fx["one"].pk}"')
        self.assertNotContains(response, "Seul Inscrit")
        self.assertIn("no-store", response["Cache-Control"])

    def test_get_filter_one_shows_name_escaped(self):
        Registration.objects.filter(event=self.fx["one"]).update(organizer_name="Seul <Nom>")
        client = _bureau_client()
        response = client.get(self.url, {"staffing": "one"})
        self.assertContains(response, f'value="{self.fx["one"].pk}"')
        self.assertContains(response, escape("Seul <Nom>"))
        self.assertNotContains(response, "Seul <Nom>")
        self.assertNotContains(response, f'value="{self.fx["empty"].pk}"')

    def test_get_invalid_filter_no_candidates(self):
        client = _bureau_client()
        response = client.get(self.url, {"staffing": "0 OR 1=1"})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "event_ids")
        self.assertContains(response, "Aucune date correspondante")

    def test_post_valid_hides_checked(self):
        client = _bureau_client()
        self.period.refresh_from_db()
        response = client.post(
            self.url,
            {
                "staffing": "none",
                "expected_revision": self.period.revision,
                "event_ids": [str(self.fx["empty"].pk)],
                "confirm_hide": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Event.objects.get(pk=self.fx["empty"].pk).is_hidden)

    def test_post_missing_confirm_ids_revision_no_mutation(self):
        client = _bureau_client()
        self.period.refresh_from_db()
        base = {
            "staffing": "none",
            "expected_revision": self.period.revision,
            "event_ids": [str(self.fx["empty"].pk)],
        }
        for overrides in (
            {},
            {"confirm_hide": "on", "event_ids": []},
            {"confirm_hide": "on", "expected_revision": "0 OR 1=1"},
            {"confirm_hide": "on", "staffing": "bogus"},
        ):
            data = {**base, **overrides}
            response = client.post(self.url, data)
            self.assertEqual(response.status_code, 200, overrides)
            self.assertFalse(Event.objects.get(pk=self.fx["empty"].pk).is_hidden)

    def test_post_stale_revision_shows_fresh_form(self):
        client = _bureau_client()
        stale = self.period.revision
        _signup(self.fx["one"], "Tardif")
        response = client.post(
            self.url,
            {
                "staffing": "none",
                "expected_revision": stale,
                "event_ids": [str(self.fx["empty"].pk)],
                "confirm_hide": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Event.objects.get(pk=self.fx["empty"].pk).is_hidden)
        self.period.refresh_from_db()
        self.assertContains(response, f'value="{self.period.revision}"')
        self.assertNotContains(response, 'id="id_confirm_hide" checked')
        self.assertNotContains(response, 'name="confirm_hide" checked')

    def test_post_csrf_required(self):
        client = Client(enforce_csrf_checks=True)
        group, _ = Group.objects.get_or_create(name="Bureau")
        user = User.objects.create_user(username="bureau", password="x")
        user.groups.add(group)
        client.force_login(user)
        self.period.refresh_from_db()
        response = client.post(
            self.url,
            {
                "staffing": "none",
                "expected_revision": self.period.revision,
                "event_ids": [str(self.fx["empty"].pk)],
                "confirm_hide": "on",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Event.objects.get(pk=self.fx["empty"].pk).is_hidden)

    def test_archived_get_redirects_post_rejected(self):
        period_services.archive(self.period.pk, actor=BUREAU)
        client = _bureau_client()
        response = client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.period.refresh_from_db()
        response = client.post(
            self.url,
            {
                "staffing": "none",
                "expected_revision": self.period.revision,
                "event_ids": [str(self.fx["empty"].pk)],
                "confirm_hide": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Event.objects.get(pk=self.fx["empty"].pk).is_hidden)
