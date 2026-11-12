import getpass
import io
import os
import tempfile
import uuid
from datetime import date
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.db import connections
from django.test import Client, TestCase, TransactionTestCase, override_settings

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
        response = Client().get(f"/bureau/exports/periode/{uuid.uuid4()}.csv")
        self.assertEqual(response.status_code, 302)

    def test_bureau_login_and_session(self):
        make_bureau()
        client = Client()
        response = client.post("/bureau/connexion/", {"password": "wrong"})
        self.assertEqual(response.status_code, 200)
        response = client.post("/bureau/connexion/", {"password": "S0lid-bureau-password-42"})
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
        response = client.post("/bureau/connexion/", {"password": "S0lid-bureau-password-42"})
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

        with (
            self.assertRaises(CommandError),
            mock.patch.object(getpass, "getpass", lambda *a, **k: next(inputs)),
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
        self.assertFalse(client.get("/bureau/").status_code == 200)
        other = Client()
        response = other.post("/bureau/connexion/", {"password": "An0ther-solid-99"})
        self.assertEqual(response.status_code, 302)


@web_client
class ExportAndBackupTests(TestCase):
    def setUp(self):
        make_bureau()
        self.client = Client()
        self.client.post("/bureau/connexion/", {"password": "S0lid-bureau-password-42"})
        self.period = periods.create_period(
            actor=BUREAU,
            label="S1",
            start_date=date(2027, 1, 1),
            end_date=date(2027, 6, 30),
            creation_key=uuid.uuid4(),
        )

    def test_csv_neutralizes_formulas(self):
        evil = organizers.get_or_create_organizer("=HYPERLINK()")
        from planning.services import registrations

        event = self.period.events.first()
        registrations.set_registration(event.pk, evil.pk, "active", actor=BUREAU, note="=cmd")
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


class BackupCommandTests(TransactionTestCase):
    def _use_file_db(self, path):
        self._original_db_name = connections["default"].settings_dict["NAME"]
        connections["default"].settings_dict["NAME"] = path
        connections.close_all()

    def _restore_file_db(self):
        connections.close_all()
        connections["default"].settings_dict["NAME"] = self._original_db_name
        call_command("migrate", run_syncdb=True, verbosity=0)

    def test_backup_real_file(self):
        try:
            self._test_backup_real_file_body()
        finally:
            self._restore_file_db()

    def _test_backup_real_file_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "live.sqlite3")
            self._use_file_db(db_path)
            with override_settings(BACKUP_DIR=tmp):
                call_command("migrate", run_syncdb=True, verbosity=0)
                periods.create_period(
                    actor=BUREAU,
                    label="S1",
                    start_date=date(2027, 1, 1),
                    end_date=date(2027, 6, 30),
                    creation_key=uuid.uuid4(),
                )
                out = io.StringIO()
                call_command("backup_database", stdout=out)
                files = [f for f in os.listdir(tmp) if f.startswith("athcalendar-")]
                self.assertEqual(len(files), 1)
                import sqlite3

                conn = sqlite3.connect(os.path.join(tmp, files[0]))
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM planning_period").fetchone()[0],
                    1,
                )
                conn.close()
            self._restore_file_db()

    def test_backup_never_overwrites(self):
        try:
            self._test_backup_never_overwrites_body()
        finally:
            self._restore_file_db()

    def _test_backup_never_overwrites_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "live.sqlite3")
            self._use_file_db(db_path)
            with override_settings(BACKUP_DIR=tmp):
                call_command("migrate", run_syncdb=True, verbosity=0)
                call_command("backup_database", stdout=io.StringIO())
                call_command("backup_database", stdout=io.StringIO())
                files = [f for f in os.listdir(tmp) if f.startswith("athcalendar-")]
                self.assertEqual(len(files), 2)
            self._restore_file_db()

    def test_backup_missing_source_fails(self):
        try:
            self._test_backup_missing_source_fails_body()
        finally:
            self._restore_file_db()

    def _test_backup_missing_source_fails_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "absent.sqlite3")
            from django.core.management.base import CommandError

            self._use_file_db(missing)
            with override_settings(BACKUP_DIR=tmp):
                with self.assertRaises(CommandError):
                    call_command("backup_database", stdout=io.StringIO())
                self.assertEqual(
                    [f for f in os.listdir(tmp) if f.startswith("athcalendar-")],
                    [],
                )
            self._restore_file_db()

    def test_backup_deadline_aborts(self):
        try:
            self._test_backup_deadline_body()
        finally:
            self._restore_file_db()

    def _test_backup_deadline_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "live.sqlite3")
            from django.core.management.base import CommandError

            self._use_file_db(db_path)
            with override_settings(BACKUP_DIR=tmp):
                call_command("migrate", run_syncdb=True, verbosity=0)
                untouched = os.path.join(tmp, "athcalendar-keep.sqlite3")
                with open(untouched, "wb") as handle:
                    handle.write(b"keep")
                module = __import__(
                    "planning.management.commands.backup_database",
                    fromlist=["DEADLINE_SECONDS"],
                )
                with (
                    mock.patch.object(module, "DEADLINE_SECONDS", -1),
                    self.assertRaises(CommandError),
                ):
                    call_command("backup_database", stdout=io.StringIO())
                files = [
                    f
                    for f in os.listdir(tmp)
                    if f.startswith("athcalendar-") and f != "athcalendar-keep.sqlite3"
                ]
                self.assertEqual(files, [])
                with open(untouched, "rb") as handle:
                    self.assertEqual(handle.read(), b"keep")
            self._restore_file_db()

    def test_backup_name_collision_preserves_existing(self):
        try:
            self._test_backup_collision_body()
        finally:
            self._restore_file_db()

    def _test_backup_collision_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "live.sqlite3")
            from django.core.management.base import CommandError

            self._use_file_db(db_path)
            with override_settings(BACKUP_DIR=tmp):
                call_command("migrate", run_syncdb=True, verbosity=0)
                fixed = uuid.uuid4()
                module = __import__(
                    "planning.management.commands.backup_database",
                    fromlist=["uuid"],
                )
                with (
                    mock.patch.object(module.uuid, "uuid4", return_value=fixed),
                    mock.patch.object(
                        module, "datetime", wraps=__import__("datetime").datetime
                    ) as dt,
                ):
                    dt.now.return_value = __import__("datetime").datetime(
                        2030, 1, 1, tzinfo=__import__("datetime").UTC
                    )
                    call_command("backup_database", stdout=io.StringIO())
                    first = [f for f in os.listdir(tmp) if f.startswith("athcalendar-")]
                    self.assertEqual(len(first), 1)
                    with self.assertRaises(CommandError):
                        call_command("backup_database", stdout=io.StringIO())
                    remaining = [f for f in os.listdir(tmp) if f.startswith("athcalendar-")]
                    self.assertEqual(remaining, first)
                    size = os.path.getsize(os.path.join(tmp, first[0]))
                    self.assertGreater(size, 0)
            self._restore_file_db()
