import json
import uuid
from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase

from planning.models import AuditEntry, Event, Organizer, Registration
from planning.services import events, organizers, periods
from planning.services import registrations as registration_services
from planning.services.actors import Actor
from planning.tests import web_client

BUREAU = Actor.bureau()


def _weekday(day):
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _make_period(label, start):
    return periods.create_period(
        actor=BUREAU,
        label=label,
        start_date=start,
        end_date=start + timedelta(days=90),
        creation_key=uuid.uuid4(),
    )


def _bureau_client():
    group, _ = Group.objects.get_or_create(name="Bureau")
    user = User.objects.create_user(username="bureau-x", password="x")
    user.groups.add(group)
    client = Client()
    client.force_login(user)
    return client


@web_client
class ExperienceLevelApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        start = date.today() + timedelta(days=1)
        self.period = _make_period("S1 niveaux", start)
        evs = list(self.period.events.order_by("date"))
        self.event = evs[0]
        self.second = evs[1]
        self.url = f"/api/events/{self.event.pk}/registration/"
        self.org = organizers.get_or_create_organizer("Alice", actor=Actor.declared("Alice"))

    def _put(self, organizer, event=None, state="active", expected_version=0, **extra):
        event = event or self.event
        body = {
            "organizer_id": str(organizer.pk),
            "state": state,
            "expected_event_version": Event.objects.get(pk=event.pk).version,
            "expected_version": expected_version,
        }
        body.update(extra)
        return self.client.put(
            f"/api/events/{event.pk}/registration/",
            data=json.dumps(body),
            content_type="application/json",
        )

    def _reg(self, event, organizer=None):
        return Registration.objects.get(event=event, organizer=organizer or self.org)

    def _level(self, organizer=None):
        return Organizer.objects.get(pk=(organizer or self.org).pk).experience_level

    def test_default_level_is_unknown(self):
        self.assertEqual(self.org.experience_level, "")
        response = self._put(self.org)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._reg(self.event).experience_level, "")
        self.assertEqual(response.json()["registration"]["experience_level"], "")

    def test_explicit_beginner_sets_profile_and_snapshot(self):
        response = self._put(self.org, experience_level="beginner")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._level(), "beginner")
        self.assertEqual(self._reg(self.event).experience_level, "beginner")
        self.assertTrue(AuditEntry.objects.filter(action="organizer.experience_level").exists())

    def test_omitted_choice_reuses_saved_profile(self):
        self._put(self.org, experience_level="organizer")
        response = self._put(self.org, event=self.second)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._reg(self.second).experience_level, "organizer")

    def test_omitted_choice_preserves_existing_snapshot(self):
        self._put(self.org, experience_level="beginner")
        # Bureau force un autre instantané sur cette date
        registration_services.set_registration(
            self.event.pk,
            self.org.pk,
            "active",
            actor=BUREAU,
            experience_level="beginner",
        )
        reg = self._reg(self.event)
        reg.experience_level = "organizer"
        reg.save()
        response = self._put(self.org, expected_version=reg.version, note="ok")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._reg(self.event).experience_level, "organizer")
        self.assertEqual(self._level(), "beginner")

    def test_identical_choice_is_noop(self):
        self._put(self.org, experience_level="beginner")
        reg = self._reg(self.event)
        self.period.refresh_from_db()
        revision = self.period.revision
        audits = AuditEntry.objects.count()
        response = self._put(self.org, expected_version=reg.version, experience_level="beginner")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._reg(self.event).version, reg.version)
        self.period.refresh_from_db()
        self.assertEqual(self.period.revision, revision)
        self.assertEqual(AuditEntry.objects.count(), audits)

    def test_explicit_choice_updates_profile_despite_same_snapshot(self):
        # Inscrit « organisateur » sur deux dates, puis la première est
        # fermée et le profil requalifié « débutant » via la seconde
        # (propagation impossible vers la date fermée).
        self._put(self.org, experience_level="organizer")
        self._put(self.org, event=self.second, experience_level="organizer")
        events.set_event_availability(
            self.event.pk,
            actor=BUREAU,
            availability="unavailable",
            expected_version=self.event.version,
        )
        reg_b = self._reg(self.second)
        self._put(
            self.org,
            event=self.second,
            expected_version=reg_b.version,
            experience_level="beginner",
        )
        self.assertEqual(self._level(), "beginner")
        self.event.refresh_from_db()
        events.set_event_availability(
            self.event.pk,
            actor=BUREAU,
            availability="available",
            expected_version=self.event.version,
        )
        # L'instantané affiche déjà « organisateur », mais le profil canonique
        # est « débutant » : le choix explicite doit réaligner le profil et
        # propager aux autres dates éditables.
        reg = self._reg(self.event)
        self.assertEqual(reg.experience_level, "organizer")
        self.assertEqual(reg.state, "active")
        response = self._put(self.org, expected_version=reg.version, experience_level="organizer")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._level(), "organizer")
        self.assertEqual(self._reg(self.second).experience_level, "organizer")

    def test_explicit_choice_stale_version_blocks_profile_change(self):
        self._put(self.org, experience_level="organizer")
        self._put(self.org, event=self.second, experience_level="organizer")
        events.set_event_availability(
            self.event.pk,
            actor=BUREAU,
            availability="unavailable",
            expected_version=self.event.version,
        )
        reg_b = self._reg(self.second)
        self._put(
            self.org,
            event=self.second,
            expected_version=reg_b.version,
            experience_level="beginner",
        )
        self.event.refresh_from_db()
        events.set_event_availability(
            self.event.pk,
            actor=BUREAU,
            availability="available",
            expected_version=self.event.version,
        )
        response = self._put(self.org, expected_version=99, experience_level="organizer")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._level(), "beginner")
        self.assertEqual(self._reg(self.second).experience_level, "beginner")

    def test_archived_period_and_past_snapshots_frozen(self):
        other_period = _make_period("S2 archivée", date.today() + timedelta(days=200))
        other_event = other_period.events.order_by("date").first()
        self._put(self.org)
        self._put(self.org, event=other_event)
        other_reg = self._reg(other_event)
        periods.archive(other_period.pk, actor=BUREAU)
        past_event = Event.objects.create(period=self.period, date=date.today() - timedelta(days=1))
        past_reg = registration_services.set_registration(
            past_event.pk, self.org.pk, "active", actor=BUREAU
        )
        other_period.refresh_from_db()
        archived_revision = other_period.revision
        reg = self._reg(self.event)
        response = self._put(self.org, expected_version=reg.version, experience_level="beginner")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._level(), "beginner")
        other_reg.refresh_from_db()
        past_reg.refresh_from_db()
        other_period.refresh_from_db()
        self.assertEqual(other_reg.experience_level, "")
        self.assertEqual(other_reg.version, 1)
        self.assertEqual(past_reg.experience_level, "")
        self.assertEqual(past_reg.version, 1)
        self.assertEqual(other_period.revision, archived_revision)

    def test_public_level_does_not_grant_bureau(self):
        self._put(self.org, experience_level="organizer")
        response = self.client.get("/bureau/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("connexion", response["Location"])

    def test_grade_only_bumps_registration_not_event(self):
        self._put(self.org)
        reg = self._reg(self.event)
        event_version = self.event.version
        response = self._put(self.org, expected_version=reg.version, experience_level="organizer")
        self.assertEqual(response.status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(self.event.version, event_version)
        self.assertEqual(self._reg(self.event).version, reg.version + 1)

    def test_invalid_levels_rejected_without_mutation(self):
        self._put(self.org)
        reg = self._reg(self.event)
        for bad in (None, "", True, ["beginner"], {"x": 1}, "expert", "Organisateur"):
            response = self._put(self.org, expected_version=reg.version, experience_level=bad)
            self.assertEqual(response.status_code, 400, bad)
        self.assertEqual(self._level(), "")
        self.assertEqual(self._reg(self.event).experience_level, "")
        self.assertEqual(self._reg(self.event).version, reg.version)

    def test_withdrawal_with_level_rejected(self):
        self._put(self.org)
        reg = self._reg(self.event)
        response = self._put(
            self.org,
            state="withdrawn",
            expected_version=reg.version,
            experience_level="beginner",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._reg(self.event).state, "active")

    def test_grade_only_stale_registration_conflicts(self):
        self._put(self.org)
        reg = self._reg(self.event)
        response = self._put(self.org, expected_version=99, experience_level="beginner")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._level(), "")
        self.assertEqual(self._reg(self.event).experience_level, "")
        self.assertEqual(self._reg(self.event).version, reg.version)

    def test_stale_event_version_conflicts(self):
        response = self.client.put(
            self.url,
            data=json.dumps(
                {
                    "organizer_id": str(self.org.pk),
                    "state": "active",
                    "expected_event_version": 99,
                    "expected_version": 0,
                    "experience_level": "beginner",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._level(), "")
        self.assertFalse(Registration.objects.filter(event=self.event, organizer=self.org).exists())

    def test_locked_dates_reject_without_profile_change(self):
        today = date.today()
        past_event = Event.objects.create(
            period=self.period,
            date=today - timedelta(days=1),
            type_label="",
        )
        cancelled = events.create_event(
            self.period.pk, actor=BUREAU, date_value=_weekday(today + timedelta(days=40))
        )
        events.cancel_event(cancelled.pk, actor=BUREAU)
        full = events.create_event(
            self.period.pk, actor=BUREAU, date_value=_weekday(today + timedelta(days=45))
        )
        events.set_event_availability(
            full.pk, actor=BUREAU, availability="full", expected_version=full.version
        )
        unavailable = events.create_event(
            self.period.pk, actor=BUREAU, date_value=_weekday(today + timedelta(days=50))
        )
        events.set_event_availability(
            unavailable.pk,
            actor=BUREAU,
            availability="unavailable",
            expected_version=unavailable.version,
        )
        hidden = events.create_event(
            self.period.pk, actor=BUREAU, date_value=_weekday(today + timedelta(days=55))
        )
        events.set_event_hidden(
            hidden.pk, actor=BUREAU, is_hidden=True, expected_version=hidden.version
        )
        for event in (past_event, cancelled, full, unavailable, hidden):
            response = self._put(self.org, event=event, experience_level="beginner")
            self.assertIn(response.status_code, (409, 404), event.pk)
        self.assertEqual(self._level(), "")
        self.assertFalse(
            Registration.objects.filter(
                event__in=[past_event, cancelled, full, unavailable, hidden]
            ).exists()
        )

    def test_capacity_failure_leaves_profile(self):
        bob = organizers.get_or_create_organizer("Bob", actor=Actor.declared("Bob"))
        self._put(bob)
        self.event.min_organizers = 1
        self.event.capacity = 1
        self.event.save()
        response = self._put(self.org, experience_level="beginner")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._level(), "")

    def test_two_beginners_coexist_and_count_normally(self):
        bob = organizers.get_or_create_organizer("Bob", actor=Actor.declared("Bob"))
        self.assertEqual(self._put(self.org, experience_level="beginner").status_code, 200)
        self.assertEqual(self._put(bob, experience_level="beginner").status_code, 200)
        self.event.refresh_from_db()
        regs = self.event.registrations.filter(state="active")
        self.assertEqual(regs.count(), 2)
        self.assertEqual(set(regs.values_list("experience_level", flat=True)), {"beginner"})

    def test_propagation_to_upcoming_regs_across_periods(self):
        other_period = _make_period("S2 niveaux", date.today() + timedelta(days=200))
        other_event = other_period.events.order_by("date").first()
        self._put(self.org)
        self._put(self.org, event=self.second)
        self._put(self.org, event=other_event)
        rev_before = other_period.revision
        reg = self._reg(self.event)
        response = self._put(self.org, expected_version=reg.version, experience_level="organizer")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._level(), "organizer")
        for event in (self.second, other_event):
            propagated = self._reg(event)
            self.assertEqual(propagated.experience_level, "organizer")
            self.assertGreater(propagated.version, 1)
            event.refresh_from_db()
        other_period.refresh_from_db()
        self.assertGreater(other_period.revision, rev_before)

    def test_public_propagation_skips_closed_and_past(self):
        today = date.today()
        full = events.create_event(
            self.period.pk, actor=BUREAU, date_value=_weekday(today + timedelta(days=40))
        )
        events.set_event_availability(
            full.pk, actor=BUREAU, availability="full", expected_version=full.version
        )
        cancelled = events.create_event(
            self.period.pk, actor=BUREAU, date_value=_weekday(today + timedelta(days=45))
        )
        events.cancel_event(cancelled.pk, actor=BUREAU)
        hidden = events.create_event(
            self.period.pk, actor=BUREAU, date_value=_weekday(today + timedelta(days=50))
        )
        events.set_event_hidden(
            hidden.pk, actor=BUREAU, is_hidden=True, expected_version=hidden.version
        )
        unavailable = events.create_event(
            self.period.pk, actor=BUREAU, date_value=_weekday(today + timedelta(days=55))
        )
        events.set_event_availability(
            unavailable.pk,
            actor=BUREAU,
            availability="unavailable",
            expected_version=unavailable.version,
        )
        past_event = Event.objects.create(period=self.period, date=today - timedelta(days=1))
        # Bureau inscrit le pseudo sur des dates fermées/passées.
        for event in (full, cancelled, unavailable, hidden, past_event):
            registration_services.set_registration(event.pk, self.org.pk, "active", actor=BUREAU)
        self._put(self.org)
        reg = self._reg(self.event)
        response = self._put(self.org, expected_version=reg.version, experience_level="beginner")
        self.assertEqual(response.status_code, 200)
        for event in (full, cancelled, unavailable, hidden, past_event):
            self.assertEqual(self._reg(event).experience_level, "")

    def test_bureau_propagates_to_closed_upcoming(self):
        today = date.today()
        full = events.create_event(
            self.period.pk, actor=BUREAU, date_value=_weekday(today + timedelta(days=40))
        )
        events.set_event_availability(
            full.pk, actor=BUREAU, availability="full", expected_version=full.version
        )
        registration_services.set_registration(full.pk, self.org.pk, "active", actor=BUREAU)
        registration_services.set_registration(
            self.event.pk,
            self.org.pk,
            "active",
            actor=BUREAU,
            experience_level="organizer",
        )
        self.assertEqual(self._reg(full).experience_level, "organizer")
        self.assertEqual(self._level(), "organizer")

    def test_archived_period_blocks_everything(self):
        self._put(self.org)
        periods.archive(self.period.pk, actor=BUREAU)
        response = self._put(self.org, expected_version=1, experience_level="beginner")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._level(), "")
        self.assertEqual(self._reg(self.event).experience_level, "")

    def test_reactivation_copies_current_profile(self):
        self._put(self.org)
        reg = self._reg(self.event)
        response = self._put(self.org, state="withdrawn", expected_version=reg.version)
        self.assertEqual(response.status_code, 200)
        withdrawn = self._reg(self.event)
        self.assertEqual(withdrawn.experience_level, "")
        response = self._put(self.org, event=self.second, experience_level="organizer")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._level(), "organizer")
        response = self._put(self.org, expected_version=withdrawn.version)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._reg(self.event).experience_level, "organizer")

    def test_alias_uses_canonical_profile(self):
        canonical_org = organizers.get_or_create_organizer("Alice Canon", actor=BUREAU)
        response = self._put(canonical_org, event=self.second, experience_level="beginner")
        self.assertEqual(response.status_code, 200)
        alias = organizers.get_or_create_organizer("Alice Alias", actor=BUREAU)
        organizers.merge_organizers(alias.pk, canonical_org.pk, actor=BUREAU)
        response = self._put(alias)
        self.assertEqual(response.status_code, 200)
        reg = Registration.objects.get(event=self.event, organizer=canonical_org)
        self.assertEqual(reg.experience_level, "beginner")

    def test_bureau_form_correction(self):
        self._put(self.org)
        client = _bureau_client()
        response = client.post(
            f"/bureau/dates/{self.event.pk}/",
            {
                "action": "registration",
                "organizer": str(self.org.pk),
                "state": "active",
                "experience_level": "beginner",
                "note": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._reg(self.event).experience_level, "beginner")
        self.assertEqual(self._level(), "beginner")

    def test_organizer_create_does_not_set_level(self):
        response = self.client.post(
            "/api/organizers/create/",
            data=json.dumps({"name": "Carole"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        organizer = Organizer.objects.get(name="Carole")
        self.assertEqual(organizer.experience_level, "")


class MigrationUpgradeTests(TransactionTestCase):
    """Montée 0006 -> 0007 : données existantes préservées, niveaux vides."""

    def test_upgrade_preserves_registrations(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("planning", "0006_alter_gametype_code")])
        try:
            apps = executor.loader.project_state([("planning", "0006_alter_gametype_code")]).apps
            OldPeriod = apps.get_model("planning", "Period")
            OldEvent = apps.get_model("planning", "Event")
            OldOrganizer = apps.get_model("planning", "Organizer")
            OldReg = apps.get_model("planning", "Registration")
            period = OldPeriod.objects.create(
                creation_key=uuid.uuid4(),
                label="S1 migration",
                start_date=date.today(),
                end_date=date.today() + timedelta(days=60),
            )
            event = OldEvent.objects.create(period=period, date=date.today() + timedelta(days=7))
            organizer = OldOrganizer.objects.create(name="Ancien", normalized_key="ancien")
            reg = OldReg.objects.create(
                event=event,
                organizer=organizer,
                organizer_name="Ancien",
                note="note conservée",
            )
            executor = MigrationExecutor(connection)
            executor.migrate([("planning", "0007_experience_levels")])
            reg = Registration.objects.get(pk=reg.pk)
            self.assertEqual(reg.experience_level, "")
            self.assertEqual(reg.organizer_name, "Ancien")
            self.assertEqual(reg.note, "note conservée")
            self.assertEqual(reg.state, "active")
            self.assertEqual(reg.version, 1)
            self.assertEqual(Organizer.objects.get(pk=organizer.pk).experience_level, "")
            self.assertEqual(Event.objects.get(pk=event.pk).pk, event.pk)
        finally:
            MigrationExecutor(connection).migrate([("planning", "0007_experience_levels")])
