import uuid
from datetime import date
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.utils import timezone

from planning.errors import ServiceError, VersionConflict
from planning.models import (
    AuditEntry,
    Event,
    GameType,
    Organizer,
    Period,
    Registration,
    SiteSettings,
)
from planning.services import registrations
from planning.services.actors import Actor
from planning.services.period_deletion import delete_period, deletion_preview
from planning.tests import web_client


def _fixture():
    period_a = Period.objects.create(
        creation_key=uuid.uuid4(),
        label="Semestre test",
        start_date=date(2030, 1, 1),
        end_date=date(2030, 6, 30),
    )
    period_b = Period.objects.create(
        creation_key=uuid.uuid4(),
        label="Autre période",
        start_date=date(2031, 1, 1),
        end_date=date(2031, 6, 30),
    )
    game_type = GameType.objects.get(code="flex")
    organizer = Organizer.objects.create(name="Alice", normalized_key="alice")
    e1 = Event.objects.create(period=period_a, date=date(2030, 1, 5), game_type=game_type)
    e2 = Event.objects.create(
        period=period_a,
        date=date(2030, 1, 12),
        game_type=game_type,
        is_hidden=True,
        status=Event.Status.CANCELLED,
    )
    e3 = Event.objects.create(period=period_b, date=date(2031, 1, 4), game_type=game_type)
    Registration.objects.create(event=e1, organizer=organizer, organizer_name="Alice")
    Registration.objects.create(
        event=e1,
        organizer=Organizer.objects.create(name="Bob", normalized_key="bob"),
        organizer_name="Bob",
    )
    Registration.objects.create(
        event=e2,
        organizer=organizer,
        organizer_name="Alice",
        state=Registration.State.WITHDRAWN,
    )
    Registration.objects.create(event=e3, organizer=organizer, organizer_name="Alice")
    settings = SiteSettings.get()
    settings.active_period = period_a
    settings.save(update_fields=["active_period"])
    return period_a, period_b, game_type, organizer


class PeriodDeletionServiceTests(TestCase):
    def setUp(self):
        self.a, self.b, self.game_type, self.organizer = _fixture()
        self.bureau = Actor.bureau()

    def _delete_kwargs(self, **overrides):
        period = Period.objects.get(pk=self.a.pk)
        kwargs = {
            "actor": self.bureau,
            "expected_version": period.version,
            "expected_revision": period.revision,
            "confirmation_label": period.label,
            "confirm_delete": True,
        }
        kwargs.update(overrides)
        return kwargs

    def test_preview_counts_everything_and_is_read_only(self):
        preview = deletion_preview(self.a.pk, actor=self.bureau)
        self.assertEqual(preview.event_count, 2)
        self.assertEqual(preview.registration_count, 3)
        self.assertTrue(preview.is_active)
        self.assertEqual(Event.objects.filter(period=self.a).count(), 2)
        self.assertEqual(Registration.objects.filter(event__period=self.a).count(), 3)

    def test_delete_removes_only_target_period_and_logs_audit(self):
        target_id = str(self.a.pk)
        delete_period(self.a.pk, **self._delete_kwargs())
        self.assertFalse(Period.objects.filter(pk=self.a.pk).exists())
        self.assertEqual(Event.objects.filter(period=self.b).count(), 1)
        self.assertEqual(Registration.objects.filter(event__period=self.b).count(), 1)
        self.assertEqual(Organizer.objects.count(), 2)
        self.assertTrue(GameType.objects.filter(code="flex").exists())
        self.assertIsNone(SiteSettings.get().active_period_id)
        entry = AuditEntry.objects.get(action="period.delete")
        self.assertEqual(entry.target_type, "Period")
        self.assertEqual(entry.target_id, target_id)
        self.assertIsNone(entry.after)
        self.assertEqual(entry.before["event_count"], 2)
        self.assertEqual(entry.before["registration_count"], 3)
        self.assertTrue(entry.before["was_active"])

    def test_delete_empty_period_and_non_active_keeps_pointer(self):
        empty = Period.objects.create(
            creation_key=uuid.uuid4(),
            label="Vide",
            start_date=date(2032, 1, 1),
            end_date=date(2032, 1, 31),
        )
        settings = SiteSettings.get()
        settings.active_period = self.b
        settings.save(update_fields=["active_period"])
        delete_period(
            empty.pk,
            actor=self.bureau,
            expected_version=empty.version,
            expected_revision=empty.revision,
            confirmation_label="Vide",
            confirm_delete=True,
        )
        self.assertFalse(Period.objects.filter(pk=empty.pk).exists())
        self.assertEqual(SiteSettings.get().active_period_id, self.b.pk)

    def test_non_bureau_actors_are_forbidden(self):
        for actor in (Actor.system(), Actor.public(self.organizer)):
            with self.assertRaises(ServiceError):
                deletion_preview(self.a.pk, actor=actor)
            with self.assertRaises(ServiceError):
                delete_period(self.a.pk, **self._delete_kwargs(actor=actor))
        self.assertTrue(Period.objects.filter(pk=self.a.pk).exists())

    def _assert_rejected_and_unchanged(self, **overrides):
        with self.assertRaises(ServiceError):
            delete_period(self.a.pk, **self._delete_kwargs(**overrides))
        self.assertTrue(Period.objects.filter(pk=self.a.pk).exists())
        self.assertEqual(Registration.objects.filter(event__period=self.a).count(), 3)
        self.assertFalse(AuditEntry.objects.filter(action="period.delete").exists())

    def test_confirmation_guards_reject_without_mutation(self):
        self._assert_rejected_and_unchanged(confirmation_label="Mauvais libellé")
        self._assert_rejected_and_unchanged(confirmation_label="")
        self._assert_rejected_and_unchanged(confirm_delete=False)
        self._assert_rejected_and_unchanged(confirm_delete="oui")
        self._assert_rejected_and_unchanged(expected_version="abc")
        self._assert_rejected_and_unchanged(expected_version=0)
        self._assert_rejected_and_unchanged(expected_version=True)
        self._assert_rejected_and_unchanged(expected_version=False)
        self._assert_rejected_and_unchanged(expected_revision="x")
        self._assert_rejected_and_unchanged(expected_version=self.a.version + 1)
        self._assert_rejected_and_unchanged(expected_revision=self.a.revision + 1)

    def test_stale_revision_after_new_registration_is_rejected(self):
        preview = deletion_preview(self.a.pk, actor=self.bureau)
        charlie = Organizer.objects.create(name="Charlie", normalized_key="charlie")
        event = Event.objects.get(period=self.a, is_hidden=False, status=Event.Status.SCHEDULED)
        registrations.set_registration(event.pk, charlie.pk, "active", actor=self.bureau)
        self.assertEqual(Registration.objects.filter(event__period=self.a).count(), 4)
        period = Period.objects.get(pk=self.a.pk)
        with self.assertRaises(VersionConflict):
            delete_period(
                self.a.pk,
                actor=self.bureau,
                expected_version=period.version,
                expected_revision=preview.period.revision,
                confirmation_label=period.label,
                confirm_delete=True,
            )
        self.assertTrue(Period.objects.filter(pk=self.a.pk).exists())
        self.assertEqual(Registration.objects.filter(event__period=self.a).count(), 4)
        self.assertFalse(AuditEntry.objects.filter(action="period.delete").exists())

    def test_archived_period_must_be_reopened_first(self):
        self.a.archived_at = timezone.now()
        self.a.save(update_fields=["archived_at"])
        with self.assertRaises(ServiceError):
            delete_period(self.a.pk, **self._delete_kwargs())
        self.assertTrue(Period.objects.filter(pk=self.a.pk).exists())

    def test_audit_and_delete_are_atomic_on_failure(self):
        with (
            patch.object(Period, "delete", side_effect=RuntimeError("boom")),
            self.assertRaises(RuntimeError),
        ):
            delete_period(self.a.pk, **self._delete_kwargs())
        self.assertTrue(Period.objects.filter(pk=self.a.pk).exists())
        self.assertEqual(Event.objects.filter(period=self.a).count(), 2)
        self.assertEqual(Registration.objects.filter(event__period=self.a).count(), 3)
        self.assertFalse(AuditEntry.objects.filter(action="period.delete").exists())


@web_client
class PeriodDeletionWebTests(TestCase):
    def setUp(self):
        self.a, self.b, self.game_type, self.organizer = _fixture()
        group, _ = Group.objects.get_or_create(name="Bureau")
        user = User.objects.create_user(username="bureau")
        user.groups.add(group)
        self.bureau = Client()
        self.bureau.force_login(user)
        self.url = f"/bureau/periodes/{self.a.pk}/supprimer/"

    def _post(self, **overrides):
        period = Period.objects.get(pk=self.a.pk)
        data = {
            "confirmation_label": period.label,
            "confirm_delete": "on",
            "expected_version": str(period.version),
            "expected_revision": str(period.revision),
        }
        data.update(overrides)
        return self.bureau.post(self.url, data)

    def test_anonymous_get_and_post_redirect_to_login(self):
        self.assertRedirects(
            Client().get(self.url), "/bureau/connexion/", fetch_redirect_response=False
        )
        self.assertRedirects(
            Client().post(self.url, {}), "/bureau/connexion/", fetch_redirect_response=False
        )
        self.assertTrue(Period.objects.filter(pk=self.a.pk).exists())

    def test_post_without_csrf_is_rejected(self):
        client = Client(enforce_csrf_checks=True)
        user = User.objects.get(username="bureau")
        client.force_login(user)
        self.assertEqual(client.post(self.url, {}).status_code, 403)
        self.assertTrue(Period.objects.filter(pk=self.a.pk).exists())

    def test_get_shows_counts_and_form(self):
        response = self.bureau.get(self.url)
        self.assertContains(response, "Supprimer définitivement la période")
        self.assertContains(response, "2 date(s), 3 inscription(s)")
        self.assertContains(response, 'name="confirmation_label"')
        self.assertContains(response, "période est active")

    def test_successful_post_deletes_and_redirects_to_dashboard(self):
        response = self._post()
        self.assertRedirects(response, "/bureau/", fetch_redirect_response=False)
        self.assertFalse(Period.objects.filter(pk=self.a.pk).exists())
        self.assertEqual(Period.objects.count(), 1)

    def test_wrong_label_keeps_records_and_resets_form(self):
        response = self._post(confirmation_label="autre")
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertTrue(Period.objects.filter(pk=self.a.pk).exists())
        follow = self.bureau.get(response["Location"])
        self.assertContains(follow, "Recopiez exactement")

    def test_missing_acknowledgment_renders_errors_and_keeps_records(self):
        period = Period.objects.get(pk=self.a.pk)
        response = self.bureau.post(
            self.url,
            {
                "confirmation_label": period.label,
                "expected_version": str(period.version),
                "expected_revision": str(period.revision),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "obligatoire")
        self.assertTrue(Period.objects.filter(pk=self.a.pk).exists())

    def test_stale_post_resets_form_with_fresh_counts(self):
        period = Period.objects.get(pk=self.a.pk)
        charlie = Organizer.objects.create(name="Charlie", normalized_key="charlie")
        event = Event.objects.get(period=self.a, is_hidden=False, status=Event.Status.SCHEDULED)
        registrations.set_registration(event.pk, charlie.pk, "active", actor=Actor.bureau())
        response = self.bureau.post(
            self.url,
            {
                "confirmation_label": period.label,
                "confirm_delete": "on",
                "expected_version": str(period.version),
                "expected_revision": str(period.revision),
            },
        )
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertTrue(Period.objects.filter(pk=self.a.pk).exists())
        self.assertEqual(Registration.objects.filter(event__period=self.a).count(), 4)
        fresh = Period.objects.get(pk=self.a.pk)
        follow = self.bureau.get(response["Location"])
        self.assertContains(follow, "2 date(s), 4 inscription(s)")
        self.assertContains(follow, f'value="{fresh.revision}"')
        self.assertNotContains(follow, f'value="{period.label}"')
        self.assertNotContains(follow, "checked")

    def test_archived_get_shows_notice_without_form(self):
        self.a.archived_at = timezone.now()
        self.a.save(update_fields=["archived_at"])
        response = self.bureau.get(self.url)
        self.assertContains(response, "Rouvrez d")
        self.assertNotContains(response, 'name="confirmation_label"')
        malicious = self.bureau.post(self.url, {"confirmation_label": "x"})
        self.assertNotContains(malicious, 'name="confirmation_label"')
        self.assertTrue(Period.objects.filter(pk=self.a.pk).exists())

    def test_script_label_is_escaped(self):
        self.a.label = "<script>alert(1)</script>"
        self.a.save(update_fields=["label"])
        response = self.bureau.get(self.url)
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertContains(response, "&lt;script&gt;")

    def test_external_next_parameter_is_not_honoured(self):
        response = self._post(**{"next": "https://evil.example/"})
        self.assertEqual(response["Location"], "/bureau/")

    def test_public_surfaces_with_remaining_period(self):
        removed = Event.objects.filter(period=self.a).first().pk
        kept = Event.objects.filter(period=self.b).first().pk
        self._post()
        response = Client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Client().get("/healthz").status_code, 200)
        self.assertEqual(Client().get(f"/dates/{removed}/").status_code, 404)
        self.assertEqual(Client().get(f"/dates/{kept}/").status_code, 200)

    def test_public_surfaces_after_all_periods_deleted(self):
        bureau = Actor.bureau()
        for pk in (self.a.pk, self.b.pk):
            period = Period.objects.get(pk=pk)
            delete_period(
                pk,
                actor=bureau,
                expected_version=period.version,
                expected_revision=period.revision,
                confirmation_label=period.label,
                confirm_delete=True,
            )
        self.assertFalse(Period.objects.exists())
        self.assertEqual(Client().get("/").status_code, 200)
        self.assertEqual(Client().get("/healthz").status_code, 200)
