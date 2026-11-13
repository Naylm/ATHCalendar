import json
import uuid
from datetime import date
from unittest import mock

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase, override_settings

from planning import selectors
from planning.errors import Conflict, ValidationFailed
from planning.middleware import TrustedProxyMiddleware
from planning.models import GameType
from planning.services import organizers, periods, registrations
from planning.services import types as type_services
from planning.services.actors import Actor
from planning.tests import web_client

BUREAU = Actor.bureau()


def make_period(start=date(2027, 1, 1), end=date(2027, 6, 30), **kwargs):
    return periods.create_period(
        actor=BUREAU,
        label="S1",
        start_date=start,
        end_date=end,
        creation_key=uuid.uuid4(),
        **kwargs,
    )


@web_client
class CsrfCookieTests(TestCase):
    def test_get_sets_cookie_and_write_succeeds(self):
        client = Client(enforce_csrf_checks=True)
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("csrftoken", response.cookies)
        token = response.cookies["csrftoken"].value
        created = client.post(
            "/api/organizers/create/",
            data='{"name": "Nouveau Pseudo"}',
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(created.status_code, 201)


@web_client
class StrictApiTests(TestCase):
    def setUp(self):
        self.period = make_period()
        self.event = self.period.events.first()
        self.organizer = organizers.get_or_create_organizer("Api User")

    def test_organizer_create_rejects_non_string(self):
        for payload in ({"name": 42}, {"name": None}, {"name": ["x"]}, {}):
            response = Client().post(
                "/api/organizers/create/",
                data=json.dumps(payload),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 400, payload)
            self.assertEqual(response.json()["error"]["code"], "invalid")

    def test_registration_version_types_strict(self):
        url = f"/api/events/{self.event.pk}/registration/"
        for extra in (
            {"expected_event_version": True, "expected_version": 0},
            {"expected_event_version": 0, "expected_version": 0},
            {"expected_event_version": "1", "expected_version": 0},
            {"expected_event_version": self.event.version},
            {"expected_event_version": self.event.version, "expected_version": -1},
        ):
            body = {
                "organizer_id": str(self.organizer.pk),
                "state": "active",
                **extra,
            }
            response = Client().put(
                url,
                data=json.dumps(body),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 400, extra)

    def test_privileged_keys_rejected(self):
        url = f"/api/events/{self.event.pk}/registration/"
        body = {
            "organizer_id": str(self.organizer.pk),
            "state": "active",
            "expected_event_version": self.event.version,
            "expected_version": 0,
            "is_bureau": True,
        }
        response = Client().put(
            url,
            data=json.dumps(body),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_batch_versions_strict(self):
        other = self.period.events.all()[1]
        url = "/api/registrations/batch/"
        base = {
            "organizer_id": str(self.organizer.pk),
            "event_ids": [str(self.event.pk), str(other.pk)],
            "state": "active",
        }
        cases = [
            {},
            {"versions": {}},
            {"versions": {str(self.event.pk): {"event": self.event.version}}},
            {
                "versions": {
                    str(self.event.pk): {"event": self.event.version, "registration": 0},
                    str(other.pk): {"event": "x", "registration": 0},
                }
            },
            {
                "versions": {
                    str(self.event.pk): {"event": self.event.version, "registration": True},
                    str(other.pk): {"event": other.version, "registration": 0},
                }
            },
        ]
        for extra in cases:
            body = {**base, **extra}
            response = Client().post(
                url,
                data=json.dumps(body),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 400, extra)
        ok = {
            str(self.event.pk): {"event": self.event.version, "registration": 0},
            str(other.pk): {"event": other.version, "registration": 0},
        }
        response = Client().post(
            url,
            data=json.dumps({**base, "versions": ok}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

    def test_event_detail_404(self):
        response = Client().get(f"/dates/{uuid.uuid4()}/")
        self.assertEqual(response.status_code, 404)


@web_client
class FilterSelectorTests(TestCase):
    def test_filters(self):
        period = make_period(start=date(2026, 1, 3), end=date(2026, 1, 31))
        future = period.events.filter(date__gte=date(2026, 1, 17))
        cancelled = future.first()
        from planning.services import events as event_services

        event_services.cancel_event(cancelled.pk, actor=BUREAU)
        org = organizers.get_or_create_organizer("Filter U")
        target = future.exclude(pk=cancelled.pk).first()
        registrations.set_registration(target.pk, org.pk, "active", actor=BUREAU)
        today = date(2026, 1, 15)
        avenir, tag = selectors.filtered_events(period, "avenir", today=today)
        self.assertTrue(all(e.date >= today for e in avenir))
        renfort, tag = selectors.filtered_events(period, "renfort", today=today)
        self.assertNotIn(cancelled, list(renfort))
        self.assertTrue(all(e.date >= today and e.status == "scheduled" for e in renfort))
        toutes, tag = selectors.filtered_events(period, "toutes", today=today)
        self.assertEqual(toutes.count(), period.events.count())
        periods.archive(period.pk, actor=BUREAU)
        default_all, tag = selectors.filtered_events(
            periods.get_period(period.pk), None, today=today
        )
        self.assertEqual(tag, "toutes")
        self.assertEqual(default_all.count(), period.events.count())

    def test_api_filtre_param(self):
        period = make_period()
        response = Client().get(f"/api/periods/{period.pk}/planning/?filtre=renfort")
        self.assertEqual(response.status_code, 200)
        response = Client().get(f"/api/periods/{period.pk}/planning/")
        self.assertEqual(len(response.json()["events"]), period.events.count())


class PeriodFormFlowTests(TestCase):
    def setUp(self):
        group, _ = Group.objects.get_or_create(name="Bureau")
        self.user = User.objects.create_user(username="bureau", password="S0lid-bureau-password-42")
        self.user.groups.add(group)
        self.client = Client()
        self.client.post("/bureau/connexion/", {"password": "S0lid-bureau-password-42"})

    @web_client
    def test_create_requires_previewed_flag(self):
        response = self.client.post(
            "/bureau/periodes/nouvelle/",
            {
                "label": "Semaine seule",
                "start_date": "2027-03-08",
                "end_date": "2027-03-12",
                "creation_key": str(uuid.uuid4()),
                "action": "create",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "Prévisualisez les week-ends avant de créer le planning.", count=1
        )
        from planning.models import Period

        self.assertFalse(Period.objects.filter(label="Semaine seule").exists())
        response = self.client.post(
            "/bureau/periodes/nouvelle/",
            {
                "label": "Semaine seule",
                "start_date": "2027-03-08",
                "end_date": "2027-03-12",
                "creation_key": str(uuid.uuid4()),
                "action": "create",
                "previewed": "1",
                "selected_dates": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        from planning.models import Period

        period = Period.objects.get(label="Semaine seule")
        self.assertEqual(period.events.count(), 0)


class TypeServiceTests(TestCase):
    def test_create_duplicate_code_conflict(self):
        with self.assertRaises(Conflict):
            type_services.create_type(actor=BUREAU, code="flex", name="Bis", color="#6b7c4a")

    def test_color_rejected_outside_palette(self):
        with self.assertRaises(ValidationFailed):
            type_services.create_type(actor=BUREAU, code="x1", name="X", color="#123456")

    def test_rename_updates_nonarchived_events(self):
        game_type = GameType.objects.get(code="flex")
        period = make_period()
        event = period.events.first()
        event.game_type = game_type
        event.type_label = game_type.name
        event.save()
        old_version = event.version
        type_services.update_type(game_type.pk, actor=BUREAU, name="Flex V2")
        event.refresh_from_db()
        self.assertEqual(event.type_label, "Flex V2")
        self.assertEqual(event.version, old_version + 1)
        period.refresh_from_db()
        self.assertGreater(period.revision, 0)

    def test_rename_preserves_archived_snapshot(self):
        game_type = GameType.objects.get(code="flex")
        period = make_period()
        event = period.events.first()
        event.game_type = game_type
        event.type_label = "Ancien libellé"
        event.save()
        periods.archive(period.pk, actor=BUREAU)
        type_services.update_type(game_type.pk, actor=BUREAU, name="Flex V3")
        event.refresh_from_db()
        self.assertEqual(event.type_label, "Ancien libellé")

    def test_inactive_type_assignment_rules(self):
        from planning.services import events as event_services

        game_type = GameType.objects.get(code="flex")
        type_services.set_type_active(game_type.pk, actor=BUREAU, active=False)
        period = make_period()
        with self.assertRaises(ValidationFailed):
            event_services.create_event(
                period.pk,
                actor=BUREAU,
                date_value=date(2027, 2, 2),
                game_type_id=game_type.pk,
            )


class BureauBatchScopeTests(TestCase):
    def setUp(self):
        group, _ = Group.objects.get_or_create(name="Bureau")
        self.user = User.objects.create_user(username="bureau", password="S0lid-bureau-password-42")
        self.user.groups.add(group)
        self.client = Client()
        self.client.post("/bureau/connexion/", {"password": "S0lid-bureau-password-42"})
        self.period = make_period()
        self.other = make_period(start=date(2027, 7, 1), end=date(2027, 12, 31))

    @web_client
    def test_foreign_event_rejected(self):
        foreign = self.other.events.first()
        response = self.client.post(
            f"/bureau/periodes/{self.period.pk}/dates/groupe/",
            {
                "event_ids": [str(foreign.pk)],
                "min_organizers": "3",
            },
        )
        self.assertEqual(response.status_code, 302)
        foreign.refresh_from_db()
        self.assertNotEqual(foreign.min_organizers, 3)

    @web_client
    def test_stale_versions_rejected(self):
        event = self.period.events.first()
        stale = event.version + 5
        response = self.client.post(
            f"/bureau/periodes/{self.period.pk}/dates/groupe/",
            {
                "event_ids": [str(event.pk)],
                "min_organizers": "3",
                "versions_json": json.dumps({str(event.pk): stale}),
            },
        )
        self.assertEqual(response.status_code, 302)
        event.refresh_from_db()
        self.assertNotEqual(event.min_organizers, 3)


@web_client
class JournalScopeTests(TestCase):
    def test_period_filter_excludes_other_period(self):
        group, _ = Group.objects.get_or_create(name="Bureau")
        user = User.objects.create_user(username="bureau", password="S0lid-bureau-password-42")
        user.groups.add(group)
        client = Client()
        client.post("/bureau/connexion/", {"password": "S0lid-bureau-password-42"})
        p1 = make_period()
        p2 = make_period(start=date(2027, 7, 1), end=date(2027, 12, 31))
        org = organizers.get_or_create_organizer("Journal U")
        registrations.set_registration(p2.events.first().pk, org.pk, "active", actor=BUREAU)
        response = client.get(f"/bureau/journal/?period={p1.pk}")
        entries = response.context["entries"]
        reg_target = str(p2.events.first().registrations.first().pk)
        self.assertFalse(any(e.target_id == reg_target for e in entries))


class TrustedProxyTests(TestCase):
    def _request(self, remote, xff=None, xfp=None):
        from django.test import RequestFactory

        meta = {"REMOTE_ADDR": remote}
        if xff is not None:
            meta["HTTP_X_FORWARDED_FOR"] = xff
        if xfp is not None:
            meta["HTTP_X_FORWARDED_PROTO"] = xfp
        request = RequestFactory().get("/", **meta)
        return request

    def _run(self, request, proxies):
        with override_settings(TRUSTED_PROXIES=proxies):
            mw = TrustedProxyMiddleware(lambda r: r)
            return mw(request)

    def test_untrusted_xff_stripped(self):
        request = self._request("10.0.0.9", xff="1.2.3.4", xfp="https")
        result = self._run(request, ["10.0.0.1"])
        self.assertEqual(result.source_ip, "10.0.0.9")
        self.assertNotIn("HTTP_X_FORWARDED_FOR", result.META)
        self.assertNotIn("HTTP_X_FORWARDED_PROTO", result.META)

    def test_trusted_chain_right_to_left(self):
        request = self._request("10.0.0.2", xff="9.9.9.9, 203.0.113.5, 10.0.0.1")
        result = self._run(request, ["10.0.0.0/24"])
        self.assertEqual(result.source_ip, "203.0.113.5")

    def test_spoofed_leftmost_ignored(self):
        request = self._request("10.0.0.2", xff="6.6.6.6, 7.7.7.7, 10.0.0.1")
        result = self._run(request, ["10.0.0.0/24"])
        self.assertEqual(result.source_ip, "7.7.7.7")

    def test_all_trusted_falls_back_remote(self):
        request = self._request("10.0.0.2", xff="10.0.0.1")
        result = self._run(request, ["10.0.0.0/24"])
        self.assertEqual(result.source_ip, "10.0.0.2")


class WeekendEdgeTests(TestCase):
    def test_no_overflow(self):
        self.assertEqual(
            periods.weekend_dates(date(2026, 1, 1), date(2026, 1, 1)),
            [],
        )
        self.assertEqual(
            periods.weekend_dates(date(2026, 1, 3), date(2026, 1, 3)),
            [date(2026, 1, 3)],
        )

    def test_date_max_no_overflow(self):
        self.assertEqual(periods.weekend_dates(date.max, date.max), [])
        self.assertEqual(
            periods.weekend_dates(date(9999, 12, 25), date.max),
            [date(9999, 12, 25), date(9999, 12, 26)],
        )


@web_client
class HealthzTests(TestCase):
    def test_healthz_ok(self):
        self.assertEqual(Client().get("/healthz").status_code, 200)

    def test_healthz_503_when_schema_missing(self):
        response = Client().get("/healthz")
        self.assertEqual(response.status_code, 200)
        with mock.patch(
            "django.db.backends.utils.CursorWrapper.execute",
            side_effect=Exception("boom"),
        ):
            response = Client().get("/healthz")
            self.assertEqual(response.status_code, 503)


class MissingWeekendTests(TestCase):
    def test_subset_generation(self):
        period = make_period(
            start=date(2027, 1, 1),
            end=date(2027, 1, 10),
            selected_dates=[date(2027, 1, 2)],
        )
        missing = periods.missing_weekend_dates(period)
        self.assertIn(date(2027, 1, 3), missing)
        created = periods.complete_missing_weekends(
            period.pk, actor=BUREAU, selected_dates=[date(2027, 1, 3)]
        )
        self.assertEqual(created, [date(2027, 1, 3)])
        self.assertTrue(period.events.filter(date=date(2027, 1, 3)).exists())
        with self.assertRaises(ValidationFailed):
            periods.complete_missing_weekends(
                period.pk,
                actor=BUREAU,
                selected_dates=[date(2027, 1, 5)],
            )


@web_client
class PeriodFormReworkTests(TestCase):
    def setUp(self):
        group, _ = Group.objects.get_or_create(name="Bureau")
        self.user = User.objects.create_user(username="bureau", password="S0lid-bureau-password-42")
        self.user.groups.add(group)
        self.client = Client()
        self.client.post("/bureau/connexion/", {"password": "S0lid-bureau-password-42"})

    def test_missing_creation_key_rejected(self):
        response = self.client.post(
            "/bureau/periodes/nouvelle/",
            {
                "label": "Sans clé",
                "start_date": "2027-01-04",
                "end_date": "2027-06-30",
                "action": "create",
                "previewed": "1",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "obligatoire", status_code=200)
        from planning.models import Period

        self.assertFalse(Period.objects.filter(label="Sans clé").exists())

    def test_inverted_range_renders_errors(self):
        response = self.client.post(
            "/bureau/periodes/nouvelle/",
            {
                "label": "Inversée",
                "start_date": "2027-06-01",
                "end_date": "2027-01-01",
                "creation_key": str(uuid.uuid4()),
                "action": "create",
                "previewed": "1",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "fin", status_code=200)
        from planning.models import Period

        self.assertFalse(Period.objects.filter(label="Inversée").exists())

    def test_double_create_same_key_single_period(self):
        key = str(uuid.uuid4())
        payload = {
            "label": "Idempotente",
            "start_date": "2027-01-04",
            "end_date": "2027-03-31",
            "creation_key": key,
            "action": "create",
            "previewed": "1",
            "selected_dates": "",
        }
        first = self.client.post("/bureau/periodes/nouvelle/", payload)
        second = self.client.post("/bureau/periodes/nouvelle/", payload)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        from planning.models import Period

        self.assertEqual(Period.objects.filter(label="Idempotente").count(), 1)

    def test_zero_selected_missing_dates_adds_nothing(self):
        period = make_period(start=date(2027, 3, 1), end=date(2027, 3, 8))
        for event in period.events.all():
            event.delete()
        response = self.client.post(
            f"/bureau/periodes/{period.pk}/",
            {"action": "generate_missing", "missing_dates": []},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(period.events.count(), 0)

    def test_single_missing_date_added(self):
        from planning.models import Event
        from planning.services.periods import weekend_dates

        period = make_period(start=date(2027, 3, 1), end=date(2027, 3, 15))
        days = weekend_dates(period.start_date, period.end_date)
        target = days[0]
        Event.objects.filter(period=period, date=target).delete()
        self.client.post(
            f"/bureau/periodes/{period.pk}/",
            {"action": "generate_missing", "missing_dates": [target.isoformat()]},
        )
        self.assertTrue(period.events.filter(date=target).exists())


@web_client
class BulkVersionMapTests(TestCase):
    def setUp(self):
        group, _ = Group.objects.get_or_create(name="Bureau")
        self.user = User.objects.create_user(username="bureau", password="S0lid-bureau-password-42")
        self.user.groups.add(group)
        self.client = Client()
        self.client.post("/bureau/connexion/", {"password": "S0lid-bureau-password-42"})
        self.period = make_period()
        self.event = self.period.events.first()
        self.url = f"/bureau/periodes/{self.period.pk}/dates/groupe/"

    def _post(self, versions_raw):
        return self.client.post(
            self.url,
            {
                "event_ids": [str(self.event.pk)],
                "min_organizers": "7",
                "versions_json": versions_raw,
            },
        )

    def test_empty_map_does_not_bypass(self):
        self._post("{}")
        self.event.refresh_from_db()
        self.assertNotEqual(self.event.min_organizers, 7)

    def test_partial_map_missing_key_rejected(self):
        other_event = self.period.events.exclude(pk=self.event.pk).first()
        self.client.post(
            self.url,
            {
                "event_ids": [str(self.event.pk), str(other_event.pk)],
                "min_organizers": "7",
                "versions_json": json.dumps({str(self.event.pk): self.event.version}),
            },
        )
        self.event.refresh_from_db()
        other_event.refresh_from_db()
        self.assertNotEqual(self.event.min_organizers, 7)
        self.assertNotEqual(other_event.min_organizers, 7)

    def test_invalid_map_rejected(self):
        self._post("{not json")
        self.event.refresh_from_db()
        self.assertNotEqual(self.event.min_organizers, 7)

    def test_valid_map_applies(self):
        self._post(json.dumps({str(self.event.pk): self.event.version}))
        self.event.refresh_from_db()
        self.assertEqual(self.event.min_organizers, 7)


@web_client
class JournalFilterErrorTests(TestCase):
    def setUp(self):
        group, _ = Group.objects.get_or_create(name="Bureau")
        self.user = User.objects.create_user(username="bureau", password="S0lid-bureau-password-42")
        self.user.groups.add(group)
        self.client = Client()
        self.client.post("/bureau/connexion/", {"password": "S0lid-bureau-password-42"})

    def test_invalid_period_filter_200(self):
        response = self.client.get("/bureau/journal/?period=pas-un-uuid")
        self.assertEqual(response.status_code, 200)

    def test_invalid_event_filter_200(self):
        response = self.client.get("/bureau/journal/?event=pas-un-uuid")
        self.assertEqual(response.status_code, 200)


class TypeColorRevisionTests(TestCase):
    def test_color_only_change_bumps_period_revision(self):
        from planning.models import Event

        period = make_period()
        game_type = type_services.create_type(
            actor=BUREAU,
            code="CLR",
            name="Couleur",
            color="#6b7c4a",
        )
        event = period.events.first()
        Event.objects.filter(pk=event.pk).update(game_type=game_type)
        revision_before = period.revision
        type_services.update_type(game_type.pk, actor=BUREAU, color="#b08a3e")
        period.refresh_from_db()
        self.assertGreater(period.revision, revision_before)

    def test_event_payload_has_type_class(self):
        from planning.models import Event

        period = make_period()
        game_type = type_services.create_type(
            actor=BUREAU,
            code="CLS",
            name="Classe",
            color="#526777",
        )
        event = period.events.first()
        Event.objects.filter(pk=event.pk).update(game_type=game_type)
        event.refresh_from_db()
        payload = selectors.event_payload(event)
        self.assertEqual(payload["type_class"], "type-c3")


@web_client
class SiteSettingsViewTests(TestCase):
    def setUp(self):
        group, _ = Group.objects.get_or_create(name="Bureau")
        self.user = User.objects.create_user(username="bureau", password="S0lid-bureau-password-42")
        self.user.groups.add(group)
        self.client = Client()
        self.client.post("/bureau/connexion/", {"password": "S0lid-bureau-password-42"})

    def test_settings_update_persists(self):
        response = self.client.post(
            "/bureau/reglages/",
            {
                "site_name": "Nouveau nom",
                "default_min_organizers": "3",
                "default_capacity": "8",
            },
        )
        self.assertEqual(response.status_code, 302)

        from planning.models import SiteSettings

        site = SiteSettings.get()
        self.assertEqual(site.site_name, "Nouveau nom")
        self.assertEqual(site.default_min_organizers, 3)
        self.assertEqual(site.default_capacity, 8)


class SettingsDefaultsTests(TestCase):
    def test_min_zero_rejected_no_change(self):
        from planning.models import SiteSettings

        before = SiteSettings.get()
        with self.assertRaises(ValidationFailed):
            periods.update_settings(
                actor=BUREAU,
                site_name="X",
                default_min_organizers=0,
                default_capacity=None,
            )
        before.refresh_from_db()
        self.assertEqual(before.default_min_organizers, 2)

    def test_capacity_below_min_rejected(self):
        with self.assertRaises(ValidationFailed):
            periods.update_settings(
                actor=BUREAU,
                site_name="X",
                default_min_organizers=4,
                default_capacity=2,
            )

    def test_settings_audit_snapshot_fields(self):
        from planning.models import AuditEntry

        periods.update_settings(
            actor=BUREAU,
            site_name="Audité",
            default_min_organizers=3,
            default_capacity=None,
        )
        entry = AuditEntry.objects.filter(action="settings.update").last()
        self.assertEqual(entry.after["site_name"], "Audité")
        self.assertIn("default_min_organizers", entry.before)

    def test_new_generation_uses_new_defaults_existing_untouched(self):
        from planning.services import events as event_services

        period = make_period(start=date(2027, 3, 1), end=date(2027, 3, 31))
        existing = period.events.first()
        existing_min = existing.min_organizers
        periods.update_settings(
            actor=BUREAU,
            site_name="X",
            default_min_organizers=5,
            default_capacity=9,
        )
        new_event = event_services.create_event(
            period.pk,
            actor=BUREAU,
            date_value=date(2027, 3, 10),
        )
        self.assertEqual(new_event.min_organizers, 5)
        self.assertEqual(new_event.capacity, 9)
        existing.refresh_from_db()
        self.assertEqual(existing.min_organizers, existing_min)

    def test_create_event_capacity_paths(self):
        from planning.models import SiteSettings
        from planning.services import events as event_services

        period = make_period(start=date(2027, 3, 1), end=date(2027, 3, 31))
        default_event = event_services.create_event(
            period.pk, actor=BUREAU, date_value=date(2027, 3, 10)
        )
        self.assertEqual(default_event.capacity, SiteSettings.get().default_capacity)
        unlimited = event_services.create_event(
            period.pk, actor=BUREAU, date_value=date(2027, 3, 11), capacity=None
        )
        self.assertIsNone(unlimited.capacity)
        explicit = event_services.create_event(
            period.pk, actor=BUREAU, date_value=date(2027, 3, 12), capacity=4
        )
        self.assertEqual(explicit.capacity, 4)


@web_client
class GameTypeLabelTests(TestCase):
    def test_str_and_form_option_label(self):
        game_type = type_services.create_type(
            actor=BUREAU, code="LIBRE", name="Flex", color="#6b7c4a"
        )
        self.assertEqual(str(game_type), "Flex")
        from planning import forms

        form = forms.EventForm()
        html = str(form["game_type"])
        self.assertIn("Flex", html)
        self.assertNotIn("GameType object", html)


@web_client
class SiteNameContextTests(TestCase):
    def test_default_site_name(self):
        response = Client().get("/")
        self.assertContains(response, "Calendrier organisateurs ATH")

    def test_renamed_site_name_escaped(self):
        from planning.models import SiteSettings

        site = SiteSettings.get()
        site.site_name = "Mon <Club> & Cie"
        site.save()
        response = Client().get("/")
        content = response.content.decode()
        self.assertIn("Mon &lt;Club&gt; &amp; Cie", content)
        self.assertNotIn("<Club>", content)
