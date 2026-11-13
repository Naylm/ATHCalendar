import uuid
from datetime import date
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.utils import timezone

from planning import forms
from planning.errors import Conflict, ServiceError, ValidationFailed
from planning.models import AuditEntry, Event, GameType, Organizer, Period, Registration
from planning.services import events as event_services
from planning.services import types as type_services
from planning.services.actors import Actor
from planning.tests import web_client


def _period(**kwargs):
    return Period.objects.create(
        creation_key=uuid.uuid4(),
        label="Période test",
        start_date=date(2030, 1, 1),
        end_date=date(2030, 6, 30),
        **kwargs,
    )


def _unused_type(**kwargs):
    return GameType.objects.create(
        code=kwargs.pop("code", "event.spé"),
        name=kwargs.pop("name", "Evènement spécial"),
        **kwargs,
    )


class TypeCodeServiceTests(TestCase):
    def setUp(self):
        self.bureau = Actor.bureau()

    def _create(self, code="event.spé", name="Evènement spécial", **kwargs):
        return type_services.create_type(
            actor=self.bureau, code=code, name=name, color="#60766b", **kwargs
        )

    def test_accented_dotted_code_is_stored_verbatim(self):
        game_type = self._create()
        self.assertEqual(game_type.code, "event.spé")
        self.assertEqual(game_type.name, "Evènement spécial")
        self.assertEqual(game_type.color, "#60766b")
        fresh = GameType.objects.get(pk=game_type.pk)
        fresh.full_clean()
        self.assertEqual(fresh.code, "event.spé")

    def test_nfc_canonicalizes_decomposed_input(self):
        game_type = self._create(code="event.spe\u0301")
        self.assertEqual(game_type.code, "event.spé")

    def test_invalid_codes_are_rejected_without_mutation(self):
        for code in ("", "a/b", "avec espace", "x" * 41):
            with self.assertRaises(ValidationFailed):
                self._create(code=code)
        self.assertFalse(GameType.objects.filter(name="Evènement spécial").exists())

    def test_case_and_accent_insensitive_duplicates_conflict(self):
        self._create()
        for code in ("EVENT.SPÉ", "event.SPE\u0301", "event.spe\u0301"):
            with self.assertRaises(Conflict, msg=code):
                self._create(code=code)
        self.assertEqual(GameType.objects.filter(name="Evènement spécial").count(), 1)

    def test_ascii_duplicate_guard_still_applies(self):
        for code in ("flex", "FLEX"):
            with self.assertRaises(Conflict, msg=code):
                self._create(code=code)

    def test_form_rejects_invalid_code_with_field_error(self):
        form = forms.GameTypeForm(
            {
                "name": "Evènement spécial",
                "code": "a/b",
                "description": "",
                "color": "#60766b",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("code", form.errors)

    def test_form_accepts_accented_code(self):
        form = forms.GameTypeForm(
            {
                "name": "Evènement spécial",
                "code": "event.spé",
                "description": "",
                "color": "#60766b",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["code"], "event.spé")


class TypeDeletionServiceTests(TestCase):
    def setUp(self):
        self.bureau = Actor.bureau()
        self.period = _period()
        self.used = GameType.objects.get(code="flex")
        self.organizer = Organizer.objects.create(name="Alice", normalized_key="alice")
        self.event = Event.objects.create(
            period=self.period,
            date=date(2030, 1, 5),
            game_type=self.used,
            is_hidden=True,
            status=Event.Status.CANCELLED,
        )
        self.event2 = Event.objects.create(
            period=self.period, date=date(2030, 1, 12), game_type=self.used
        )
        self.period.archived_at = timezone.now()
        self.period.save(update_fields=["archived_at"])
        self.registration = Registration.objects.create(
            event=self.event, organizer=self.organizer, organizer_name="Alice"
        )

    def _delete_kwargs(self, game_type, **overrides):
        kwargs = {
            "actor": self.bureau,
            "confirmation_code": game_type.code,
            "confirm_delete": True,
        }
        kwargs.update(overrides)
        return kwargs

    def test_preview_counts_all_event_references(self):
        preview = type_services.deletion_preview(self.used.pk, actor=self.bureau)
        self.assertEqual(preview.event_count, 2)
        self.assertEqual(preview.game_type.pk, self.used.pk)

    def test_used_type_cannot_be_deleted_and_everything_is_preserved(self):
        version, revision = self.period.version, self.period.revision
        with self.assertRaises(Conflict):
            type_services.delete_type(self.used.pk, **self._delete_kwargs(self.used))
        self.assertTrue(GameType.objects.filter(pk=self.used.pk).exists())
        self.assertEqual(Event.objects.filter(game_type=self.used).count(), 2)
        self.period.refresh_from_db()
        self.assertEqual((self.period.version, self.period.revision), (version, revision))
        self.assertEqual(Registration.objects.filter(event=self.event).count(), 1)
        self.assertFalse(AuditEntry.objects.filter(action="gametype.delete").exists())

    def test_unused_type_delete_removes_only_type_and_logs_audit(self):
        unused = _unused_type(is_active=False)
        target = str(unused.pk)
        type_services.delete_type(unused.pk, **self._delete_kwargs(unused))
        self.assertFalse(GameType.objects.filter(pk=unused.pk).exists())
        self.assertEqual(Period.objects.count(), 1)
        self.assertEqual(Event.objects.count(), 2)
        self.assertEqual(Registration.objects.count(), 1)
        entry = AuditEntry.objects.get(action="gametype.delete")
        self.assertEqual(entry.target_type, "GameType")
        self.assertEqual(entry.target_id, target)
        self.assertIsNone(entry.after)

    def test_active_unused_type_can_be_deleted(self):
        unused = _unused_type(code="autre.code")
        type_services.delete_type(unused.pk, **self._delete_kwargs(unused))
        self.assertFalse(GameType.objects.filter(pk=unused.pk).exists())

    def test_reference_created_after_preview_still_blocks_delete(self):
        unused = _unused_type(code="autre.code")
        preview = type_services.deletion_preview(unused.pk, actor=self.bureau)
        self.assertEqual(preview.event_count, 0)
        fresh_period = _period()
        event_services.create_event(
            fresh_period.pk,
            actor=self.bureau,
            date_value=date(2030, 2, 1),
            game_type_id=unused.pk,
        )
        with self.assertRaises(Conflict):
            type_services.delete_type(unused.pk, **self._delete_kwargs(unused))
        self.assertTrue(GameType.objects.filter(pk=unused.pk).exists())

    def test_confirmation_guards(self):
        unused = _unused_type(code="autre.code")
        for overrides in (
            {"confirmation_code": "mauvais"},
            {"confirmation_code": ""},
            {"confirm_delete": False},
            {"confirm_delete": "oui"},
        ):
            with self.assertRaises(ServiceError, msg=overrides):
                type_services.delete_type(unused.pk, **self._delete_kwargs(unused, **overrides))
        self.assertTrue(GameType.objects.filter(pk=unused.pk).exists())

    def test_non_bureau_actors_are_forbidden(self):
        unused = _unused_type(code="autre.code")
        for actor in (Actor.system(), Actor.public(self.organizer)):
            with self.assertRaises(ServiceError):
                type_services.deletion_preview(unused.pk, actor=actor)
            with self.assertRaises(ServiceError):
                type_services.delete_type(unused.pk, **self._delete_kwargs(unused, actor=actor))

    def test_audit_and_delete_are_atomic_on_failure(self):
        unused = _unused_type(code="autre.code")
        with (
            patch.object(GameType, "delete", side_effect=RuntimeError("boom")),
            self.assertRaises(RuntimeError),
        ):
            type_services.delete_type(unused.pk, **self._delete_kwargs(unused))
        self.assertTrue(GameType.objects.filter(pk=unused.pk).exists())
        self.assertFalse(AuditEntry.objects.filter(action="gametype.delete").exists())


@web_client
class TypeManagementWebTests(TestCase):
    def setUp(self):
        group, _ = Group.objects.get_or_create(name="Bureau")
        user = User.objects.create_user(username="bureau")
        user.groups.add(group)
        self.bureau = Client()
        self.bureau.force_login(user)

    def _post_create(self, **overrides):
        data = {
            "action": "create",
            "name": "Evènement spécial",
            "code": "event.spé",
            "description": "Une soirée particulière",
            "color": "#60766b",
        }
        data.update(overrides)
        return self.bureau.post("/bureau/types/", data)

    def test_create_accented_type_via_http(self):
        response = self._post_create()
        self.assertRedirects(response, "/bureau/types/", fetch_redirect_response=False)
        game_type = GameType.objects.get(code="event.spé")
        self.assertEqual(game_type.name, "Evènement spécial")
        self.assertEqual(game_type.color, "#60766b")

    def test_invalid_create_keeps_bound_open_form_with_errors(self):
        response = self._post_create(code="a/b")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<details class="card" open>')
        self.assertContains(response, 'value="a/b"')
        self.assertContains(response, 'value="Evènement spécial"')
        self.assertContains(response, "Une soirée particulière")
        self.assertFalse(GameType.objects.filter(code="a/b").exists())

    def test_duplicate_create_shows_specific_code_error(self):
        GameType.objects.create(code="event.spé", name="Existant")
        response = self._post_create()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "déjà")
        self.assertContains(response, 'value="event.spé"')
        self.assertEqual(GameType.objects.filter(code="event.spé").count(), 1)

    def test_used_type_delete_page_shows_no_form(self):
        period = _period()
        flex = GameType.objects.get(code="flex")
        Event.objects.create(period=period, date=date(2030, 1, 5), game_type=flex)
        response = self.bureau.get(f"/bureau/types/{flex.pk}/supprimer/")
        self.assertContains(response, "utilisé sur 1 date(s)")
        self.assertContains(response, "Désactivez-le")
        self.assertNotContains(response, 'name="confirmation_code"')

    def test_unused_type_delete_flow(self):
        unused = _unused_type()
        url = f"/bureau/types/{unused.pk}/supprimer/"
        response = self.bureau.get(url)
        self.assertContains(response, "Supprimer définitivement le type")
        self.assertContains(response, "event.spé")
        self.assertTrue(GameType.objects.filter(pk=unused.pk).exists())
        response = self.bureau.post(url, {"confirmation_code": "mauvais", "confirm_delete": "on"})
        self.assertRedirects(response, url, fetch_redirect_response=False)
        self.assertTrue(GameType.objects.filter(pk=unused.pk).exists())
        response = self.bureau.post(url, {"confirmation_code": "event.spé", "confirm_delete": "on"})
        self.assertRedirects(response, "/bureau/types/", fetch_redirect_response=False)
        self.assertFalse(GameType.objects.filter(pk=unused.pk).exists())

    def test_anonymous_and_csrf_and_method_guards(self):
        unused = _unused_type()
        url = f"/bureau/types/{unused.pk}/supprimer/"
        self.assertRedirects(Client().get(url), "/bureau/connexion/", fetch_redirect_response=False)
        self.assertRedirects(
            Client().post(url, {}), "/bureau/connexion/", fetch_redirect_response=False
        )
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(User.objects.get(username="bureau"))
        self.assertEqual(csrf.post(url, {}).status_code, 403)
        self.assertEqual(self.bureau.put(url).status_code, 405)
        self.assertTrue(GameType.objects.filter(pk=unused.pk).exists())

    def test_missing_type_redirects_with_flash(self):
        response = self.bureau.get(f"/bureau/types/{uuid.uuid4()}/supprimer/")
        self.assertRedirects(response, "/bureau/types/", fetch_redirect_response=False)

    def test_script_code_is_escaped_on_confirmation_page(self):
        weird = GameType.objects.create(code="a<b", name="<script>x</script>")
        response = self.bureau.get(f"/bureau/types/{weird.pk}/supprimer/")
        self.assertNotContains(response, "<script>x</script>")
        self.assertContains(response, "&lt;script&gt;")
