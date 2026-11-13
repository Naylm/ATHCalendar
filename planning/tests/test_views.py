import uuid
from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.test import Client, TestCase

from planning.models import Event, Organizer, SiteSettings
from planning.services import events, organizers, periods, registrations
from planning.services.actors import Actor
from planning.tests import web_client


@web_client
class ViewSmokeTests(TestCase):
    def _active_period(self):
        today = date.today()
        period = periods.create_period(
            actor=Actor.bureau(),
            label="S1 test",
            start_date=today + timedelta(days=1),
            end_date=today + timedelta(days=120),
            creation_key=uuid.uuid4(),
        )
        periods.set_active(period.pk, actor=Actor.bureau())
        return period

    def test_healthz(self):
        response = Client().get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_index_empty(self):
        response = Client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Aucune date prévue.")

    def test_index_lists_events(self):
        self._active_period()
        response = Client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "S1 test")
        self.assertContains(response, "name-registration-form")
        self.assertContains(response, 'placeholder="Nom"')
        self.assertContains(response, "event-type-select")
        self.assertContains(response, '<option value="">À définir</option>')

    def test_topnav_bureau_lock_link(self):
        response = Client().get("/")
        self.assertContains(response, 'href="/bureau/"')
        self.assertContains(response, 'class="bureau-link"')
        self.assertContains(response, 'aria-label="Bureau"')
        self.assertContains(response, 'title="Accès Bureau"')
        self.assertContains(response, 'focusable="false"')
        self.assertNotContains(response, ">Bureau</a>")

    def test_noindex_header(self):
        response = Client().get("/")
        self.assertIn("noindex", response.headers["X-Robots-Tag"])

    def test_favicon_linked_and_available(self):
        response = Client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'<link rel="icon" type="image/png" href="{static("PATCH_ATH.png")}">',
        )
        self.assertTrue(finders.find("PATCH_ATH.png"))

    def test_home_heading(self):
        response = Client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<h1 id="planning-title">Planning</h1>',
            html=True,
        )
        self.assertNotContains(response, "Airsoft Team Heulinoise")
        self.assertNotContains(response, "planning-intro")

    def test_no_identity_ui_on_home(self):
        response = Client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "pseudo-modal")
        self.assertNotContains(response, "registrations.js")
        self.assertNotContains(response, "acting-bar")
        self.assertNotContains(response, "mes-inscriptions")

    def test_locked_event_has_no_form(self):
        self._active_period()
        for event in Event.objects.all():
            events.cancel_event(event.pk, actor=Actor.bureau())
        response = Client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "name-registration-form")
        self.assertNotContains(response, "event-type-select")
        self.assertContains(response, "Annulée")

    def test_full_event_shows_complet_no_form(self):
        self._active_period()
        Event.objects.update(capacity=2)
        alice = organizers.get_or_create_organizer("Alice")
        bob = organizers.get_or_create_organizer("Bob")
        for event in Event.objects.all():
            for organizer in (alice, bob):
                registrations.set_registration(
                    event.pk,
                    organizer.pk,
                    "active",
                    actor=Actor.public(organizer),
                )
        response = Client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "name-registration-form")
        self.assertContains(response, "Complet")

    def test_cancelled_event_has_no_form(self):
        self._active_period()
        for event in Event.objects.all():
            events.cancel_event(event.pk, actor=Actor.bureau())
        response = Client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "name-registration-form")
        self.assertNotContains(response, "event-type-select")
        self.assertContains(response, "Annulée")

    def test_organizer_names_escaped(self):
        self._active_period()
        event = Event.objects.order_by("date").first()
        organizer = organizers.get_or_create_organizer("Al<i>")
        registrations.set_registration(
            event.pk,
            organizer.pk,
            "active",
            actor=Actor.public(organizer),
        )
        response = Client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<span class="organizer-label">Al&lt;i&gt;</span>')
        self.assertNotContains(response, "Al<i>")

    def test_staffing_dot_counts_and_remove_buttons(self):
        self._active_period()
        all_events = list(Event.objects.order_by("date"))
        alice = organizers.get_or_create_organizer("Al<i>")
        bob = organizers.get_or_create_organizer("Bob")
        needs = all_events[1]
        ready = all_events[2]
        registrations.set_registration(needs.pk, alice.pk, "active", actor=Actor.public(alice))
        for organizer in (alice, bob):
            registrations.set_registration(
                ready.pk, organizer.pk, "active", actor=Actor.public(organizer)
            )
        locked = all_events[3]
        events.cancel_event(locked.pk, actor=Actor.bureau())
        response = Client().get("/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("dot-none", content)
        self.assertIn("Aucun inscrit", content)
        self.assertIn("dot-needs_more", content)
        self.assertIn("À compléter", content)
        self.assertIn("dot-sufficient", content)
        self.assertIn("Prêt", content)
        self.assertIn("0/2", content)
        self.assertIn("1/2", content)
        self.assertIn("2/2", content)
        self.assertIn("Al&lt;i&gt;", content)
        self.assertNotIn("Al<i>", content)
        reg = needs.registrations.get(organizer=alice)
        self.assertIn(f'class="remove-registration" data-organizer-id="{alice.pk}"', content)
        self.assertIn(f'data-registration-version="{reg.version}"', content)
        self.assertIn("Retirer", content)
        locked_row = content.split(f'id="date-{locked.pk}"', 1)[1].split("</li>", 1)[0]
        self.assertNotIn("remove-registration", locked_row)

    def test_month_grouping(self):
        today = date.today()
        period = periods.create_period(
            actor=Actor.bureau(),
            label="Longue",
            start_date=today + timedelta(days=1),
            end_date=today + timedelta(days=400),
            creation_key=uuid.uuid4(),
        )
        periods.set_active(period.pk, actor=Actor.bureau())
        expected = len({(e.date.year, e.date.month) for e in Event.objects.all()})
        response = Client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content.count(b'<section class="month-group" data-month'), expected
        )
        self.assertNotContains(response, "<details")
        self.assertNotContains(response, "<summary")
        self.assertEqual(response.content.count(b'aria-labelledby="month-title">'), 1)
        self.assertEqual(
            response.content.count(b'aria-labelledby="month-title" hidden>'),
            expected - 1,
        )
        self.assertContains(response, 'class="month-navigation"')
        self.assertContains(response, 'aria-label="Mois précédent"')
        self.assertContains(response, 'aria-label="Mois suivant"')
        self.assertContains(response, 'id="month-title"')
        keys = sorted({f"{e.date.year}-{e.date.month:02d}" for e in Event.objects.all()})
        for key in keys:
            self.assertContains(response, f'data-month="{key}"')

    def test_site_name_default(self):
        response = Client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Calendrier organisateurs ATH")

    def test_custom_site_name_preserved(self):
        settings = SiteSettings.get()
        periods.update_settings(
            actor=Actor.bureau(),
            site_name="Club Perso",
            default_min_organizers=settings.default_min_organizers,
            default_capacity=settings.default_capacity,
        )
        response = Client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Club Perso")
        self.assertNotContains(response, "Calendrier organisateurs ATH")

    def test_bureau_pages_hide_version_counters(self):
        group, _ = Group.objects.get_or_create(name="Bureau")
        user = User.objects.create_user(username="bureau", password="x")
        user.groups.add(group)
        period = self._active_period()
        event = Event.objects.order_by("date").first()
        organizer = organizers.get_or_create_organizer("Alice")
        registrations.set_registration(
            event.pk, organizer.pk, "active", actor=Actor.public(organizer)
        )
        client = Client()
        client.force_login(user)
        response = client.get(f"/bureau/periodes/{period.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertNotRegex(response.content.decode(), r"· v\d+")
        response = client.get(f"/bureau/dates/{event.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertNotRegex(response.content.decode(), r'<p class="muted">v\d+</p>')

    def test_bureau_organizers_section_removed_preserves_data(self):
        group, _ = Group.objects.get_or_create(name="Bureau")
        user = User.objects.create_user(username="bureau", password="x")
        user.groups.add(group)
        self._active_period()
        event = Event.objects.order_by("date").first()
        alice = organizers.get_or_create_organizer("Alice")
        bob = organizers.get_or_create_organizer("Bob")
        reg = registrations.set_registration(
            event.pk, alice.pk, "active", actor=Actor.public(alice)
        )
        client = Client()
        client.force_login(user)
        response = client.get("/bureau/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "/bureau/pseudos/")
        self.assertNotContains(response, ">Pseudos<")
        self.assertNotContains(response, "/bureau/journal/")
        self.assertNotContains(response, ">Journal<")
        counts = (
            alice.is_active,
            bob.is_active,
            reg.version,
            event.version,
        )
        response = client.get("/bureau/pseudos/")
        self.assertRedirects(response, "/bureau/", fetch_redirect_response=False)
        posts = (
            {"action": "create", "name": "Mallory"},
            {"action": "rename", "organizer": str(alice.pk), "name": "Eve"},
            {"action": "deactivate", "organizer": str(alice.pk)},
            {"action": "merge", "source": str(bob.pk), "target": str(alice.pk)},
        )
        for post in posts:
            response = client.post("/bureau/pseudos/", post)
            self.assertRedirects(response, "/bureau/", fetch_redirect_response=False)
        alice.refresh_from_db()
        bob.refresh_from_db()
        reg.refresh_from_db()
        event.refresh_from_db()
        self.assertEqual((alice.is_active, bob.is_active, reg.version, event.version), counts)
        self.assertEqual(alice.name, "Alice")
        self.assertFalse(Organizer.objects.filter(name="Mallory").exists())
        self.assertFalse(bob.merged_into_id)

    def test_brand_logo_rendered(self):
        response = Client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'<img src="{static("PATCH_ATH.png")}" alt="" width="64" height="64">',
            html=True,
        )

    def test_nav_aria_current_planning(self):
        response = Client().get("/")
        self.assertContains(response, '<a href="/" aria-current="page">Planning</a>')

    def test_nav_aria_current_bureau(self):
        response = Client().get("/bureau/", follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<a href="/bureau/" aria-current="page" class="bureau-link" aria-label="Bureau"',
        )
