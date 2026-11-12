import os
import shutil
import sqlite3
import tempfile
import threading
import uuid
from datetime import date

from django import db
from django.conf import settings
from django.core.management import call_command
from django.db import connections
from django.test import SimpleTestCase

from planning.errors import CapacityConflict, ClosedConflict, LockUnavailable
from planning.models import Event, Period, Registration
from planning.services import events as event_services
from planning.services import organizers, periods, registrations
from planning.services.actors import Actor

BUREAU = Actor.bureau()


class ConcurrencyTests(SimpleTestCase):
    databases = {"default"}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmpdir = tempfile.mkdtemp(prefix="ath-conc-")
        cls.dbfile = os.path.join(cls.tmpdir, "conc.sqlite3")
        cls.saved_name = settings.DATABASES["default"]["NAME"]
        cls.saved_options = dict(settings.DATABASES["default"]["OPTIONS"])
        settings.DATABASES["default"]["NAME"] = cls.dbfile
        settings.DATABASES["default"]["OPTIONS"]["timeout"] = 1
        connections.close_all()
        call_command("migrate", run_syncdb=True, verbosity=0)

    @classmethod
    def tearDownClass(cls):
        connections.close_all()
        settings.DATABASES["default"]["NAME"] = cls.saved_name
        settings.DATABASES["default"]["OPTIONS"].update(cls.saved_options)
        shutil.rmtree(cls.tmpdir, ignore_errors=True)
        call_command("migrate", run_syncdb=True, verbosity=0)
        super().tearDownClass()

    def setUp(self):
        for model in (Registration, Event, Period):
            model.objects.all().delete()
        self.period = periods.create_period(
            actor=BUREAU,
            label="conc",
            start_date=date(2027, 1, 1),
            end_date=date(2027, 6, 30),
            creation_key=uuid.uuid4(),
        )
        self.event = self.period.events.get(date=date(2027, 1, 2))

    def _run_parallel(self, funcs):
        barrier = threading.Barrier(len(funcs))
        results = [None] * len(funcs)
        errors = [None] * len(funcs)

        def worker(i, func):
            try:
                barrier.wait(timeout=5)
                results[i] = func()
            except Exception as exc:
                errors[i] = exc
            finally:
                db.connections.close_all()

        threads = [threading.Thread(target=worker, args=(i, f)) for i, f in enumerate(funcs)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        return results, errors

    def test_two_distinct_organizers_both_registered(self):
        a = organizers.get_or_create_organizer("Para A")
        b = organizers.get_or_create_organizer("Para B")
        _, errors = self._run_parallel(
            [
                lambda: registrations.set_registration(
                    self.event.pk, a.pk, "active", actor=Actor.public(a)
                ),
                lambda: registrations.set_registration(
                    self.event.pk, b.pk, "active", actor=Actor.public(b)
                ),
            ]
        )
        self.assertTrue(all(e is None for e in errors), errors)
        self.assertEqual(self.event.registrations.filter(state="active").count(), 2)

    def test_same_pair_single_registration(self):
        a = organizers.get_or_create_organizer("Solo")
        _, errors = self._run_parallel(
            [
                lambda: registrations.set_registration(
                    self.event.pk, a.pk, "active", actor=Actor.public(a)
                ),
                lambda: registrations.set_registration(
                    self.event.pk, a.pk, "active", actor=Actor.public(a)
                ),
            ]
        )
        self.assertEqual(Registration.objects.count(), 1)
        self.assertTrue(all(e is None or isinstance(e, LockUnavailable) for e in errors))

    def test_capacity_last_slot_single_winner(self):
        event_services.update_event(self.event.pk, actor=BUREAU, min_organizers=1, capacity=1)
        a = organizers.get_or_create_organizer("First")
        b = organizers.get_or_create_organizer("Second")
        _, errors = self._run_parallel(
            [
                lambda: registrations.set_registration(
                    self.event.pk, a.pk, "active", actor=Actor.public(a)
                ),
                lambda: registrations.set_registration(
                    self.event.pk, b.pk, "active", actor=Actor.public(b)
                ),
            ]
        )
        self.assertEqual(self.event.registrations.filter(state="active").count(), 1)
        losers = [e for e in errors if e is not None]
        self.assertEqual(len(losers), 1)
        self.assertIsInstance(losers[0], (CapacityConflict, LockUnavailable))

    def test_stale_note_loses(self):
        a = organizers.get_or_create_organizer("Noter")
        reg = registrations.set_registration(
            self.event.pk, a.pk, "active", actor=Actor.public(a), note="v1"
        )
        results, errors = self._run_parallel(
            [
                lambda: registrations.set_registration(
                    self.event.pk,
                    a.pk,
                    "active",
                    actor=Actor.public(a),
                    note="fresh",
                    expected_version=reg.version,
                ),
                lambda: registrations.set_registration(
                    self.event.pk,
                    a.pk,
                    "active",
                    actor=Actor.public(a),
                    note="stale",
                    expected_version=reg.version,
                ),
            ]
        )
        reg.refresh_from_db()
        notes = [r.note for r in results if r is not None]
        self.assertEqual(len(notes), 1)
        self.assertEqual(reg.note, notes[0])
        conflict_errors = [
            e for e in errors if e is not None and not isinstance(e, LockUnavailable)
        ]
        from planning.errors import VersionConflict

        self.assertTrue(
            any(isinstance(e, VersionConflict) for e in conflict_errors)
            or any(isinstance(e, LockUnavailable) for e in errors if e),
        )

    def test_close_vs_register_order(self):
        a = organizers.get_or_create_organizer("Racer")
        results, errors = self._run_parallel(
            [
                lambda: event_services.set_registrations_open(
                    self.event.pk, actor=BUREAU, is_open=False
                ),
                lambda: registrations.set_registration(
                    self.event.pk, a.pk, "active", actor=Actor.public(a)
                ),
            ]
        )
        self.event.refresh_from_db()
        active = self.event.registrations.filter(state="active").count()
        reg_error = errors[1]
        if not self.event.registrations_open:
            self.assertTrue(
                reg_error is None or isinstance(reg_error, (ClosedConflict, LockUnavailable))
            )
            self.assertIn(active, (0, 1))
        else:
            self.assertIsNone(reg_error)
            self.assertEqual(active, 1)

    def test_concurrent_generation_single_period(self):
        key = uuid.uuid4()
        results, _ = self._run_parallel(
            [
                lambda: periods.create_period(
                    actor=BUREAU,
                    label="g1",
                    start_date=date(2030, 1, 1),
                    end_date=date(2030, 6, 30),
                    creation_key=key,
                ),
                lambda: periods.create_period(
                    actor=BUREAU,
                    label="g2",
                    start_date=date(2030, 1, 1),
                    end_date=date(2030, 6, 30),
                    creation_key=key,
                ),
            ]
        )
        created = [r for r in results if r is not None]
        self.assertEqual(Period.objects.filter(creation_key=key).count(), 1)
        if len(created) == 2:
            self.assertEqual(created[0].pk, created[1].pk)

    def test_lock_exhaustion_retryable(self):
        a = organizers.get_or_create_organizer("Blocked")
        holder = sqlite3.connect(self.dbfile, timeout=5)
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("UPDATE planning_period SET revision = revision + 0 WHERE 1 = 0")
        try:
            with self.assertRaises(LockUnavailable):
                registrations.set_registration(self.event.pk, a.pk, "active", actor=Actor.public(a))
        finally:
            holder.rollback()
            holder.close()
        self.assertEqual(Registration.objects.count(), 0)
