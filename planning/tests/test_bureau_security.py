import uuid
from datetime import date

from django.contrib.auth.models import Group, User
from django.db import connection
from django.test import Client, TestCase
from django.utils.html import escape

from planning.models import AuditEntry, Event, Organizer, Period, Registration, SiteSettings
from planning.permissions import is_bureau
from planning.services import periods as period_services
from planning.services.actors import Actor
from planning.tests import web_client

BUREAU = Actor.bureau()
PASSWORD = "S0lid-bureau-password-42"


def make_bureau(password=PASSWORD):
    group, _ = Group.objects.get_or_create(name="Bureau")
    user = User.objects.create_user(username="bureau", password=password)
    user.groups.add(group)
    return user


def bureau_client(password=PASSWORD):
    user = make_bureau(password)
    client = Client()
    client.force_login(user)
    return client


def make_period(label="S1", start=date(2027, 1, 1), end=date(2027, 6, 30)):
    return period_services.create_period(
        actor=BUREAU,
        label=label,
        start_date=start,
        end_date=end,
        creation_key=uuid.uuid4(),
    )


def business_snapshot():
    return (
        list(Event.objects.order_by("pk").values()),
        list(Registration.objects.order_by("pk").values()),
        list(Period.objects.order_by("pk").values()),
        list(SiteSettings.objects.order_by("pk").values()),
    )


@web_client
class BureauInjectionTests(TestCase):
    def setUp(self):
        self.period = make_period()
        self.other = make_period("S2", date(2028, 1, 1), date(2028, 6, 30))
        self.client = bureau_client()

    def test_login_password_injection_denied(self):
        client = Client()
        for payload in ("' OR '1'='1' --", '" OR 1=1 --'):
            response = client.post("/bureau/connexion/", {"password": payload})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.wsgi_request.user.is_authenticated)
            self.assertEqual(client.get("/bureau/").status_code, 302)
        response = client.post("/bureau/connexion/", {"password": PASSWORD})
        self.assertEqual(response.status_code, 302)

    def test_settings_name_stored_literal_as_parameter(self):
        payload = "ATH'); SELECT 1; --"
        before = business_snapshot()
        captured = []

        def capture(execute, sql, params, many, context):
            captured.append((sql, params))
            return execute(sql, params, many, context)

        with connection.execute_wrapper(capture):
            response = self.client.post(
                "/bureau/reglages/",
                {
                    "site_name": payload,
                    "default_min_organizers": 3,
                    "default_capacity": "",
                },
            )
        self.assertEqual(response.status_code, 302)
        updates = [q for q in captured if "planning_sitesettings" in q[0].lower()]
        self.assertTrue(updates)
        for sql, _ in updates:
            self.assertNotIn(payload, sql)
        flat = [p for _, params in updates for p in (params or [])]
        self.assertIn(payload, flat)
        self.assertEqual(SiteSettings.get().site_name, payload)
        self.assertEqual(business_snapshot()[0:3], before[0:3])
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(Organizer.objects.count(), 0)
        settings_view = self.client.get("/bureau/reglages/")
        self.assertContains(settings_view, escape(payload))

    def test_period_label_and_event_fields_stored_literal(self):
        period = self.period
        other_before = business_snapshot()
        label = "Planning' OR '1'='1"
        response = self.client.post(
            f"/bureau/periodes/{period.pk}/",
            {
                "action": "update",
                "label": label,
                "start_date": "2027-01-01",
                "end_date": "2027-06-30",
                "expected_version": period.version,
            },
        )
        self.assertEqual(response.status_code, 302)
        period.refresh_from_db()
        self.assertEqual(period.label, label)
        event = Event.objects.filter(period=period).order_by("date").first()
        second = Event.objects.filter(period=self.other).order_by("date").first()
        title = "Titre'); SELECT 1; --"
        response = self.client.post(
            f"/bureau/dates/{event.pk}/",
            {
                "action": "update",
                "date": event.date.isoformat(),
                "game_type": "",
                "title": title,
                "public_note": "note'); SELECT 1; --",
                "min_organizers": event.min_organizers,
                "capacity": event.capacity or "",
                "expected_version": event.version,
            },
        )
        self.assertEqual(response.status_code, 302)
        event.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(event.title, title)
        self.assertEqual(event.public_note, "note'); SELECT 1; --")
        self.assertNotEqual(second.title, title)
        after = business_snapshot()
        self.assertEqual(after[1], other_before[1])
        other_pk = self.other.pk
        self.assertEqual(
            [p for p in after[2] if p["id"] == other_pk],
            [p for p in other_before[2] if p["id"] == other_pk],
        )
        self.assertEqual(
            [e for e in after[0] if e["period_id"] == other_pk],
            [e for e in other_before[0] if e["period_id"] == other_pk],
        )

    def test_journal_filters_treat_payloads_literally(self):
        hit = AuditEntry.objects.create(
            actor_type="bureau",
            actor_label="x' OR 1=1 --",
            action="event%hide",
            target_type="Event",
            target_id="abc",
        )
        AuditEntry.objects.create(
            actor_type="bureau",
            actor_label="bureau",
            action="eventZhide",
            target_type="Event",
            target_id="def",
        )
        hit_underscore = AuditEntry.objects.create(
            actor_type="bureau",
            actor_label="bureau",
            action="event_hide",
            target_type="Event",
            target_id="ghi",
        )
        response = self.client.get("/bureau/journal/", {"action": "' OR 1=1 --"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["entries"]), [])
        response = self.client.get("/bureau/journal/", {"action": "event%hide"})
        entries = list(response.context["entries"])
        self.assertEqual(entries, [hit])
        response = self.client.get("/bureau/journal/", {"action": "event_hide"})
        self.assertEqual(list(response.context["entries"]), [hit_underscore])
        response = self.client.get("/bureau/journal/", {"pseudo": "x' OR 1=1 --"})
        self.assertEqual(list(response.context["entries"]), [hit])
        response = self.client.get("/bureau/journal/", {"pseudo": "' OR 1=1 --"})
        self.assertEqual(list(response.context["entries"]), [hit])

    def test_malformed_ids_and_versions_never_mutate(self):
        before = business_snapshot()
        event = Event.objects.order_by("date").first()
        bogus = uuid.uuid4()
        responses = [
            self.client.get(f"/bureau/periodes/{bogus}/"),
            self.client.post(
                f"/bureau/dates/{event.pk}/",
                {"action": "hide", "expected_version": "1;SELECT 1"},
            ),
            self.client.post(
                f"/bureau/dates/{event.pk}/",
                {
                    "action": "update",
                    "date": "not-a-date",
                    "expected_version": "0 OR 1=1",
                },
            ),
            self.client.post(
                f"/bureau/periodes/{self.period.pk}/dates/groupe/",
                {"event_ids": ["0 OR 1=1"], "versions_json": "{}"},
            ),
            self.client.post(
                f"/bureau/periodes/{self.period.pk}/cloture/",
                {
                    "keep_event_ids": ["0 OR 1=1"],
                    "expected_revision": "0 OR 1=1",
                    "confirm_close": "on",
                },
            ),
            self.client.post("/bureau/types/", {"action": "deactivate", "type": "0 OR 1=1"}),
        ]
        for response in responses:
            self.assertIn(response.status_code, (200, 302, 400, 403, 404), response)
        self.assertEqual(business_snapshot(), before)


@web_client
class BureauAccessMatrixTests(TestCase):
    def setUp(self):
        self.period = make_period()
        self.event = Event.objects.order_by("date").first()

    def _protected_urls(self):
        return [
            ("GET", "/bureau/"),
            ("POST", "/bureau/"),
            ("GET", "/bureau/periodes/nouvelle/"),
            ("POST", "/bureau/periodes/nouvelle/"),
            ("GET", f"/bureau/periodes/{self.period.pk}/"),
            ("POST", f"/bureau/periodes/{self.period.pk}/"),
            ("GET", f"/bureau/periodes/{self.period.pk}/cloture/"),
            ("POST", f"/bureau/periodes/{self.period.pk}/cloture/"),
            ("GET", f"/bureau/periodes/{self.period.pk}/nettoyage/"),
            ("POST", f"/bureau/periodes/{self.period.pk}/nettoyage/"),
            ("GET", f"/bureau/periodes/{self.period.pk}/dates/nouvelle/"),
            ("POST", f"/bureau/periodes/{self.period.pk}/dates/nouvelle/"),
            ("POST", f"/bureau/periodes/{self.period.pk}/dates/groupe/"),
            ("GET", f"/bureau/dates/{self.event.pk}/"),
            ("POST", f"/bureau/dates/{self.event.pk}/"),
            ("GET", "/bureau/pseudos/"),
            ("POST", "/bureau/pseudos/"),
            ("GET", "/bureau/types/"),
            ("POST", "/bureau/types/"),
            ("GET", "/bureau/journal/"),
            ("GET", f"/bureau/exports/periode/{self.period.pk}.csv"),
            ("GET", "/bureau/exports/donnees.json"),
            ("GET", "/bureau/mot-de-passe/"),
            ("POST", "/bureau/mot-de-passe/"),
            ("GET", "/bureau/reglages/"),
            ("POST", "/bureau/reglages/"),
        ]

    def _assert_denied(self, client, label):
        before = business_snapshot()
        for method, url in self._protected_urls():
            response = getattr(client, method.lower())(url)
            self.assertEqual(response.status_code, 302, (label, method, url))
            self.assertIn("connexion", response["Location"], (label, method, url))
        self.assertEqual(business_snapshot(), before, label)

    def test_anonymous_everywhere_denied(self):
        self._assert_denied(Client(), "anon")

    def test_staff_superuser_without_group_denied(self):
        user = User.objects.create_user(
            username="root", password="x", is_staff=True, is_superuser=True
        )
        client = Client()
        client.force_login(user)
        self._assert_denied(client, "superuser")

    def test_inactive_bureau_user_denied(self):
        group, _ = Group.objects.get_or_create(name="Bureau")
        user = User.objects.create_user(username="ghost", password="x", is_active=False)
        user.groups.add(group)
        self.assertFalse(is_bureau(user))
        client = Client()
        client.force_login(user)
        response = client.get("/bureau/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("connexion", response["Location"])

    def test_revoked_group_blocks_existing_session(self):
        user = make_bureau()
        client = Client()
        client.force_login(user)
        self.assertEqual(client.get("/bureau/").status_code, 200)
        user.groups.clear()
        self.assertFalse(is_bureau(user))
        response = client.get("/bureau/")
        self.assertEqual(response.status_code, 302)

    def test_sql_like_route_ids_404(self):
        client = bureau_client()
        for url in (
            "/bureau/periodes/abc' OR '1'='1/",
            "/bureau/dates/' OR 1=1 --/",
            "/bureau/exports/periode/0 OR 1=1.csv",
        ):
            response = client.get(url)
            self.assertEqual(response.status_code, 404, url)
        response = client.get(f"/bureau/periodes/{uuid.uuid4()}/")
        self.assertIn(response.status_code, (302, 404))


@web_client
class BureauCsrfTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.user = make_bureau()
        self.period = make_period()
        self.event = Event.objects.order_by("date").first()

    def _token(self, client, url="/bureau/connexion/"):
        client.get(url)
        return client.cookies["csrftoken"].value

    def test_login_post_requires_csrf(self):
        token = self._token(self.client)
        response = self.client.post("/bureau/connexion/", {"password": PASSWORD})
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            "/bureau/connexion/",
            {"password": PASSWORD, "csrfmiddlewaretoken": token},
            HTTP_ORIGIN="https://evil.example",
            secure=True,
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            "/bureau/connexion/",
            {"password": PASSWORD, "csrfmiddlewaretoken": token},
            HTTP_ORIGIN="http://testserver",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/bureau/").status_code, 200)

    def test_private_post_requires_csrf(self):
        self.client.force_login(self.user)
        before = business_snapshot()
        response = self.client.post(
            "/bureau/reglages/",
            {"site_name": "X", "default_min_organizers": 2, "default_capacity": ""},
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            f"/bureau/dates/{self.event.pk}/",
            {"action": "hide", "expected_version": self.event.version},
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            f"/bureau/periodes/{self.period.pk}/cloture/",
            {
                "keep_event_ids": [],
                "expected_revision": self.period.revision,
                "confirm_close": "on",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(business_snapshot(), before)
        token = self._token(self.client, "/bureau/reglages/")
        response = self.client.post(
            "/bureau/reglages/",
            {
                "site_name": "X",
                "default_min_organizers": 2,
                "default_capacity": "",
                "csrfmiddlewaretoken": token,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SiteSettings.get().site_name, "X")


@web_client
class BureauCacheHeadersTests(TestCase):
    def setUp(self):
        self.client = bureau_client()
        self.period = make_period()
        self.event = Event.objects.order_by("date").first()

    def _assert_never_cached(self, response, url):
        control = response["Cache-Control"]
        for token in ("private", "no-store", "no-cache", "max-age=0", "must-revalidate"):
            self.assertIn(token, control, (url, control))
        self.assertIn("Expires", response, url)

    def test_private_pages_and_entrypoints_never_cached(self):
        urls = [
            "/bureau/",
            f"/bureau/periodes/{self.period.pk}/",
            f"/bureau/periodes/{self.period.pk}/cloture/",
            f"/bureau/dates/{self.event.pk}/",
            "/bureau/types/",
            "/bureau/journal/",
            f"/bureau/exports/periode/{self.period.pk}.csv",
            "/bureau/exports/donnees.json",
            "/bureau/reglages/",
            "/bureau/mot-de-passe/",
        ]
        for url in urls:
            self._assert_never_cached(self.client.get(url), url)
        anon = Client()
        self._assert_never_cached(anon.get("/bureau/connexion/"), "login")
        self._assert_never_cached(anon.post("/bureau/deconnexion/"), "logout-post")
        self._assert_never_cached(anon.get("/bureau/deconnexion/"), "logout-get-405")

    def test_public_home_not_cache_forbidden(self):
        response = Client().get("/")
        self.assertNotIn("no-store", response.get("Cache-Control", ""))
