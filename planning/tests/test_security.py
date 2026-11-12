import getpass
import io
import os
import tempfile
import uuid
from datetime import date
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from planning.services import organizers, periods
from planning.services.actors import Actor
from planning.tests import web_client

BUREAU = Actor.bureau()


def make_bureau(password="S0lid-bureau-password-42"):
    group, _ = Group.objects.get_or_create(name="Bureau")
    user = User.objects.create_user(username="bureau", password=password)
    user.groups.add(group)
    return user, password


@web_client
class BureauAccessTests(TestCase):
    def test_pages_redirect_anonymous(self):
        for url in ["/bureau/", "/bureau/pseudos/", "/bureau/types/", "/bureau/journal/"]:
            response = Client().get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn("connexion", response["Location"])

    def test_exports_blocked_anonymous(self):
        response = Client().get("/bureau/exports/donnees.json")
        self.assertEqual(response.status_code, 302)
        response = Client().get(
            f"/bureau/exports/periode/{uuid.uuid4()}.csv"
        )
        self.assertEqual(response.status_code, 302)

    def test_bureau_login_and_session(self):
        make_bureau()
        client = Client()
        response = client.post(
            "/bureau/connexion/", {"password": "wrong"}
        )
        self.assertEqual(response.status_code, 200)
        response = client.post(
            "/bureau/connexion/", {"password": "S0lid-bureau-password-42"}
        )
        self.assertEqual(response.status_code, 302)
        response = client.get("/bureau/")
        self.assertEqual(response.status_code, 200)
        response = client.post("/bureau/deconnexion/")
        response = client.get("/bureau/")
        self.assertEqual(response.status_code, 302)

    def test_pseudo_named_bureau_no_privilege(self):
        organizers.get_or_create_organizer("Bureau")
        response = Client().get("/bureau/")
        self.assertEqual(response.status_code, 302)

    def test_login_rate_limit_per_source(self):
        make_bureau()
        client = Client()
        for _ in range(5):
            client.post("/bureau/connexion/", {"password": "bad"})
        response = client.post(
            "/bureau/connexion/", {"password": "S0lid-bureau-password-42"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Trop de tentatives", status_code=200)


@web_client
class SetupBureauCommandTests(TestCase):
    def test_interactive_creation(self):
        inputs = iter(["S0lid-pass-99", "S0lid-pass-99"])
        with mock.patch.object(getpass, "getpass", lambda *a, **k: next(inputs)):
            call_command("setup_bureau", stdout=io.StringIO())
        user = User.objects.get(username="bureau")
        self.assertTrue(user.check_password("S0lid-pass-99"))
        self.assertTrue(user.groups.filter(name="Bureau").exists())

    def test_weak_password_rejected(self):
        inputs = iter(["abc", "abc"])
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            with mock.patch.object(
                getpass, "getpass", lambda *a, **k: next(inputs)
            ):
                call_command("setup_bureau", stdout=io.StringIO())
        self.assertFalse(User.objects.filter(username="bureau").exists())


@web_client
class PasswordChangeTests(TestCase):
    def test_change_requires_current_and_kills_sessions(self):
        make_bureau()
        client = Client()
        client.post("/bureau/connexion/", {"password": "S0lid-bureau-password-42"})
        response = client.post(
            "/bureau/mot-de-passe/",
            {"current_password": "bad", "new_password": "An0ther-solid-99"},
        )
        self.assertEqual(response.status_code, 200)
        response = client.post(
            "/bureau/mot-de-passe/",
            {
                "current_password": "S0lid-bureau-password-42",
                "new_password": "An0ther-solid-99",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            client.get("/bureau/").status_code == 200
        )
        other = Client()
        response = other.post(
            "/bureau/connexion/", {"password": "An0ther-solid-99"}
        )
        self.assertEqual(response.status_code, 302)


@web_client
class ExportAndBackupTests(TestCase):
    def setUp(self):
        make_bureau()
        self.client = Client()
        self.client.post(
            "/bureau/connexion/", {"password": "S0lid-bureau-password-42"}
        )
        self.period = periods.create_period(
            actor=BUREAU, label="S1",
            start_date=date(2027, 1, 1), end_date=date(2027, 6, 30),
            creation_key=uuid.uuid4(),
        )

    def test_csv_neutralizes_formulas(self):
        evil = organizers.get_or_create_organizer("=HYPERLINK()")
        from planning.services import registrations
        from planning.models import Registration

        event = self.period.events.first()
        registrations.set_registration(
            event.pk, evil.pk, "active", actor=BUREAU, note="=cmd"
        )
        response = self.client.get(f"/bureau/exports/periode/{self.period.pk}.csv")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        for line in content.splitlines():
            for cell in line.split(","):
                self.assertFalse(cell.startswith("="), cell)
        self.assertIn("'=cmd", content)

    def test_json_export_no_auth_tables(self):
        response = self.client.get("/bureau/exports/donnees.json")
        data = response.json()
        for key in ("users", "sessions", "auth", "rate_limits"):
            self.assertNotIn(key, data)
        self.assertIn("periods", data)
        self.assertIn("registrations", data)

    def test_backup_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(BACKUP_DIR=tmp):
                out = io.StringIO()
                call_command("backup_database", stdout=out)
                files = os.listdir(tmp)
                self.assertEqual(len(files), 1)
                import sqlite3

                conn = sqlite3.connect(os.path.join(tmp, files[0]))
                self.assertEqual(
                    conn.execute("PRAGMA integrity_check").fetchone()[0], "ok"
                )
                self.assertTrue(
                    conn.execute(
                        "SELECT COUNT(*) FROM planning_period"
                    ).fetchone()[0]
                    >= 1
                )
                conn.close()
