import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
SCREENSHOT_DIR = BASE_DIR / "test-results" / "e2e"
BUREAU_PASSWORD = secrets.token_urlsafe(24)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def future_weekend():
    day = date.today() + timedelta(days=14)
    while day.weekday() != 5:
        day += timedelta(days=1)
    return day


class LiveServer:
    def __init__(self):
        self.data_dir = tempfile.mkdtemp(prefix="ath-e2e-")
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        env = dict(os.environ)
        env["DJANGO_DATA_DIR"] = self.data_dir
        env["DJANGO_DEBUG"] = "true"
        self.env = env
        self.proc = None

    def manage(self, *args, input_text=None):
        result = subprocess.run(
            [sys.executable, "manage.py", *args],
            cwd=BASE_DIR,
            env=self.env,
            input=input_text,
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout.strip()

    def seed(self, script):
        self.manage("shell", "-c", script)

    def query(self, script):
        output = self.manage("shell", "-c", script)
        return output.splitlines()[-1].strip() if output else ""

    def __enter__(self):
        self.manage("migrate")
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "manage.py",
                "runserver",
                f"127.0.0.1:{self.port}",
                "--noreload",
            ],
            cwd=BASE_DIR,
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        import urllib.request

        for _ in range(100):
            try:
                urllib.request.urlopen(self.url + "/healthz", timeout=1)
                return self
            except Exception:
                time.sleep(0.2)
        raise RuntimeError("Le serveur E2E n'a pas démarré.")

    def __exit__(self, *exc):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        shutil.rmtree(self.data_dir, ignore_errors=True)


def seed_period_script():
    start = date.today() + timedelta(days=30)
    end = start + timedelta(days=90)
    return f"""
import uuid
from datetime import date
from planning.services import periods, organizers
from planning.services.actors import Actor
bureau = Actor.bureau()
p = periods.create_period(
    actor=bureau, label='Semestre test',
    start_date=date({start.year}, {start.month}, {start.day}),
    end_date=date({end.year}, {end.month}, {end.day}),
    creation_key=uuid.uuid4(),
)
o = organizers.get_or_create_organizer('Volontaire E2E')
o2 = organizers.get_or_create_organizer('Autre Pseudo')
"""


SEED_BUREAU = f"""
from django.contrib.auth.models import Group, User
group, _ = Group.objects.get_or_create(name='Bureau')
user = User.objects.create_user(username='bureau', password={BUREAU_PASSWORD!r})
user.groups.add(group)
"""


def browser_tests():
    try:
        from playwright.sync_api import sync_playwright

        return sync_playwright
    except ImportError:
        return None


class TrackedPage:
    def __init__(self, page):
        self.page = page
        self.js_errors = []
        page.on(
            "console",
            lambda msg: self.js_errors.append(msg.text) if msg.type == "error" else None,
        )
        page.on("pageerror", lambda exc: self.js_errors.append(str(exc)))
        page.on(
            "requestfailed",
            lambda req: self.js_errors.append(f"requestfailed: {req.url}"),
        )

    def assert_clean(self, allow_409=False):
        bad = [err for err in self.js_errors if not (allow_409 and "409" in err)]
        assert not bad, f"Erreurs navigateur: {bad}"


def expect_ok_response(page, method, fragment):
    return page.expect_response(
        lambda resp: (
            resp.request.method == method and fragment in resp.url and resp.status in (200, 201)
        ),
        timeout=8000,
    )


def check_no_horizontal_overflow(page):
    overflow = page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    )
    assert not overflow, "Débordement horizontal détecté"


def check_font_and_css(page):
    family = page.evaluate("getComputedStyle(document.body).fontFamily")
    assert family and "sans" in family.lower(), f"Police inattendue: {family}"
    sheet_loaded = page.evaluate(
        "document.styleSheets.length > 0 && document.styleSheets[0].cssRules.length > 0"
    )
    assert sheet_loaded, "Feuille de style non chargée"


class PublicUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if browser_tests() is None:
            raise unittest.SkipTest("playwright non installé")

    def setUp(self):
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    def _page(
        self, pw, url_path="/", viewport=(390, 844), browser_name="chromium", timezone_id=None
    ):
        browser = getattr(pw, browser_name).launch()
        kwargs = {"viewport": {"width": viewport[0], "height": viewport[1]}}
        if timezone_id:
            kwargs["timezone_id"] = timezone_id
        context = browser.new_context(**kwargs)
        page = TrackedPage(context.new_page())
        page.page.goto(self.server.url + url_path)
        return browser, page

    def test_first_signup_new_pseudo_and_picker_cancel(self):
        with LiveServer() as self.server:
            self.server.seed(seed_period_script())
            with browser_tests()() as pw:
                browser, tracked = self._page(pw)
                page = tracked.page
                before = self.server.query(
                    "from planning.models import Registration;print(Registration.objects.count())"
                )
                page.click("#toggle-batch")
                page.locator(".batch-select").first.check()
                page.click("#batch-join")
                page.click("#pseudo-close")
                after_cancel = self.server.query(
                    "from planning.models import Registration;print(Registration.objects.count())"
                )
                assert after_cancel == before
                page.click("#batch-join")
                page.fill("#pseudo-new-name", "Pseudo E2E")
                with (
                    expect_ok_response(page, "POST", "/api/registrations/batch/"),
                    expect_ok_response(page, "POST", "/api/organizers/"),
                ):
                    page.click("#pseudo-create-form button[type=submit]")
                page.wait_for_timeout(400)
                created = self.server.query(
                    "from planning.models import Registration;print(Registration.objects.count())"
                )
                assert created == "1", created
                page.wait_for_selector("#acting-bar:not([hidden])")
                page.screenshot(path=str(SCREENSHOT_DIR / "signup.png"), full_page=False)
                tracked.assert_clean()
                browser.close()

    def test_picker_existing_pseudo(self):
        with LiveServer() as self.server:
            self.server.seed(seed_period_script())
            with browser_tests()() as pw:
                browser, tracked = self._page(pw)
                page = tracked.page
                page.click("#toggle-batch")
                page.locator(".batch-select").first.check()
                page.click("#batch-join")
                with expect_ok_response(page, "POST", "/api/registrations/batch/"):
                    page.click("text=Volontaire E2E")
                regs = self.server.query(
                    "from planning.models import Registration;"
                    "print(Registration.objects.filter(state='active').count())"
                )
                assert regs == "1", regs
                page.wait_for_selector("#acting-bar:not([hidden])")
                page.screenshot(
                    path=str(SCREENSHOT_DIR / "existing-pseudo.png"),
                    full_page=False,
                )
                tracked.assert_clean()
                browser.close()

    def test_detail_join_edit_note_twice_and_withdraw(self):
        with LiveServer() as self.server:
            self.server.seed(seed_period_script())
            organizer_id = self.server.query(
                "from planning.models import Organizer;"
                "print(Organizer.objects.get(name='Volontaire E2E').pk)"
            )
            event_id = self.server.query(
                "from planning.models import Event;print(Event.objects.order_by('date').first().pk)"
            )
            with browser_tests()() as pw:
                browser, tracked = self._page(pw)
                page = tracked.page
                page.evaluate(
                    "window.ath.pseudo.set({id: '" + organizer_id + "', name: 'Volontaire E2E'})"
                )
                page.goto(self.server.url + "/dates/" + event_id + "/")
                with expect_ok_response(page, "PUT", "/registration/"):
                    page.click("#join-btn")
                page.wait_for_selector("#registration-form:not([hidden])")
                state = self.server.query(
                    "from planning.models import Registration;"
                    "print(Registration.objects.get().state)"
                )
                assert state == "active", state
                page.fill("#note-input", "première note")
                with expect_ok_response(page, "PUT", "/registration/"):
                    page.click("#save-note-btn")
                page.fill("#note-input", "seconde note")
                with expect_ok_response(page, "PUT", "/registration/"):
                    page.click("#save-note-btn")
                note = self.server.query(
                    "from planning.models import Registration;"
                    "print(repr(Registration.objects.get().note))"
                )
                assert note == "'seconde note'", note
                page.fill("#note-input", "")
                with expect_ok_response(page, "PUT", "/registration/"):
                    page.click("#save-note-btn")
                note = self.server.query(
                    "from planning.models import Registration;"
                    "print(repr(Registration.objects.get().note))"
                )
                assert note == "''", note
                page.click("#withdraw-btn")
                with expect_ok_response(page, "PUT", "/registration/"):
                    page.click("#withdraw-confirm")
                page.wait_for_selector("#join-btn:not([hidden])")
                state = self.server.query(
                    "from planning.models import Registration;"
                    "print(Registration.objects.get().state)"
                )
                assert state == "withdrawn", state
                page.screenshot(path=str(SCREENSHOT_DIR / "detail-flow.png"), full_page=False)
                tracked.assert_clean()
                browser.close()

    def test_double_join_no_duplicate(self):
        with LiveServer() as self.server:
            self.server.seed(seed_period_script())
            organizer_id = self.server.query(
                "from planning.models import Organizer;"
                "print(Organizer.objects.get(name='Volontaire E2E').pk)"
            )
            event_id = self.server.query(
                "from planning.models import Event;print(Event.objects.order_by('date').first().pk)"
            )
            with browser_tests()() as pw:
                browser, tracked = self._page(pw)
                page = tracked.page
                page.evaluate(
                    "window.ath.pseudo.set({id: '" + organizer_id + "', name: 'Volontaire E2E'})"
                )
                page.goto(self.server.url + "/dates/" + event_id + "/")
                with expect_ok_response(page, "PUT", "/registration/"):
                    page.evaluate(
                        "const b = document.getElementById('join-btn');b.click(); b.click();"
                    )
                page.wait_for_timeout(400)
                count = self.server.query(
                    "from planning.models import Registration;print(Registration.objects.count())"
                )
                assert count == "1", count
                tracked.assert_clean()
                browser.close()

    def test_detail_picker_cancel_no_mutation(self):
        with LiveServer() as self.server:
            self.server.seed(seed_period_script())
            event_id = self.server.query(
                "from planning.models import Event;print(Event.objects.order_by('date').first().pk)"
            )
            with browser_tests()() as pw:
                browser, tracked = self._page(pw, "/dates/" + event_id + "/")
                tracked.page.click("#join-btn")
                tracked.page.click("#pseudo-close")
                count = self.server.query(
                    "from planning.models import Registration;print(Registration.objects.count())"
                )
                assert count == "0", count
                tracked.assert_clean()
                browser.close()

    def test_stored_b_with_query_a_uses_a(self):
        with LiveServer() as self.server:
            self.server.seed(seed_period_script())
            id_a = self.server.query(
                "from planning.models import Organizer;"
                "print(Organizer.objects.get(name='Volontaire E2E').pk)"
            )
            id_b = self.server.query(
                "from planning.models import Organizer;"
                "print(Organizer.objects.get(name='Autre Pseudo').pk)"
            )
            event_id = self.server.query(
                "from planning.models import Event;print(Event.objects.order_by('date').first().pk)"
            )
            with browser_tests()() as pw:
                browser, tracked = self._page(pw)
                page = tracked.page
                page.evaluate("window.ath.pseudo.set({id: '" + id_b + "', name: 'Autre Pseudo'})")
                page.goto(self.server.url + "/dates/" + event_id + "/?organizer=" + id_a)
                page.wait_for_timeout(800)
                with expect_ok_response(page, "PUT", "/registration/"):
                    page.click("#join-btn")
                reg = self.server.query(
                    "from planning.models import Registration;"
                    "print(Registration.objects.get().organizer.name)"
                )
                assert reg == "Volontaire E2E", reg
                stored = page.evaluate("window.ath.pseudo.get().id")
                assert stored == id_a, stored
                tracked.assert_clean()
                browser.close()

    def test_switch_pseudo_note_not_copied(self):
        with LiveServer() as self.server:
            self.server.seed(seed_period_script())
            id_a = self.server.query(
                "from planning.models import Organizer;"
                "print(Organizer.objects.get(name='Volontaire E2E').pk)"
            )
            id_b = self.server.query(
                "from planning.models import Organizer;"
                "print(Organizer.objects.get(name='Autre Pseudo').pk)"
            )
            event_id = self.server.query(
                "from planning.models import Event;print(Event.objects.order_by('date').first().pk)"
            )
            self.server.seed(
                f"""
import uuid
from planning.services import registrations
from planning.services.actors import Actor
registrations.set_registration(
    '{event_id}', '{id_a}', 'active', actor=Actor.bureau(), note='note de A'
)
"""
            )
            with browser_tests()() as pw:
                browser, tracked = self._page(pw)
                page = tracked.page
                page.evaluate("window.ath.pseudo.set({id: '" + id_a + "', name: 'Volontaire E2E'})")
                page.goto(self.server.url + "/dates/" + event_id + "/?organizer=" + id_a)
                page.wait_for_selector("#registration-form:not([hidden])")
                page.evaluate("window.ath.pseudo.set({id: '" + id_b + "', name: 'Autre Pseudo'})")
                page.wait_for_timeout(800)
                note_value = page.eval_on_selector("#note-input", "el => el.value")
                assert note_value == "", note_value
                tracked.assert_clean()
                browser.close()

    def test_calendar_labels_exact_in_timezones(self):
        with LiveServer() as self.server:
            self.server.seed(seed_period_script())
            event_date = self.server.query(
                "from planning.models import Event;"
                "print(Event.objects.order_by('date').first().date.isoformat())"
            )
            expected_weekday = [
                "lundi",
                "mardi",
                "mercredi",
                "jeudi",
                "vendredi",
                "samedi",
                "dimanche",
            ][date.fromisoformat(event_date).weekday()]
            with browser_tests()() as pw:
                browser = pw.chromium.launch()
                for zone in ("Pacific/Kiritimati", "America/Los_Angeles"):
                    context = browser.new_context(timezone_id=zone)
                    tracked = TrackedPage(context.new_page())
                    tracked.page.goto(self.server.url + "/?vue=calendrier")
                    tracked.page.wait_for_selector(".calendar-grid .cal-day", timeout=5000)
                    labels = tracked.page.eval_on_selector_all(
                        ".cal-day", "els => els.map(e => e.dataset.date)"
                    )
                    assert event_date in labels, (zone, labels[:5])
                    aria = tracked.page.eval_on_selector(
                        f".cal-day[data-date='{event_date}']",
                        "el => el.getAttribute('aria-label')",
                    )
                    assert aria.startswith(expected_weekday), (zone, aria)
                    tracked.assert_clean()
                    context.close()
                browser.close()

    def test_viewports_no_overflow_and_font(self):
        with LiveServer() as self.server:
            self.server.seed(seed_period_script())
            with browser_tests()() as pw:
                for width, name in ((360, "w360"), (390, "w390"), (1440, "w1440")):
                    browser, tracked = self._page(pw, "/", viewport=(width, 844))
                    tracked.page.wait_for_selector(".event-card, .empty-state")
                    check_no_horizontal_overflow(tracked.page)
                    check_font_and_css(tracked.page)
                    tracked.page.screenshot(
                        path=str(SCREENSHOT_DIR / f"viewport-{name}.png"),
                        full_page=False,
                    )
                    tracked.assert_clean()
                    browser.close()

    def test_mobile_webkit_core_flow(self):
        with LiveServer() as self.server:
            self.server.seed(seed_period_script())
            event_id = self.server.query(
                "from planning.models import Event;print(Event.objects.order_by('date').first().pk)"
            )
            with browser_tests()() as pw:
                browser, tracked = self._page(
                    pw,
                    "/dates/" + event_id + "/",
                    viewport=(390, 844),
                    browser_name="webkit",
                )
                page = tracked.page
                page.click("#join-btn")
                page.fill("#pseudo-new-name", "Pseudo WebKit")
                with (
                    expect_ok_response(page, "PUT", "/registration/"),
                    expect_ok_response(page, "POST", "/api/organizers/"),
                ):
                    page.click("#pseudo-create-form button[type=submit]")
                page.wait_for_timeout(400)
                count = self.server.query(
                    "from planning.models import Registration;print(Registration.objects.count())"
                )
                assert count == "1", count
                check_no_horizontal_overflow(page)
                tracked.assert_clean()
                browser.close()

    def test_my_registrations_via_stored_pseudo(self):
        with LiveServer() as self.server:
            self.server.seed(seed_period_script())
            with browser_tests()() as pw:
                browser, tracked = self._page(pw, "/mes-inscriptions/")
                page = tracked.page
                page.click("#open-picker")
                page.click("text=Volontaire E2E")
                page.wait_for_url("**/mes-inscriptions/**")
                page.screenshot(
                    path=str(SCREENSHOT_DIR / "mes-inscriptions.png"),
                    full_page=False,
                )
                tracked.assert_clean()
                browser.close()

    def test_bureau_full_flow(self):
        with LiveServer() as self.server:
            self.server.seed(SEED_BUREAU)
            with browser_tests()() as pw:
                browser, tracked = self._page(pw, "/bureau/")
                page = tracked.page
                page.fill("input[type=password]", BUREAU_PASSWORD)
                page.click("button[type=submit]")
                page.wait_for_url("**/bureau/")
                page.click("text=Créer une période")
                start = date.today() + timedelta(days=30)
                end = start + timedelta(days=45)
                page.fill("input[name=label]", "Période E2E")
                page.fill("input[name=start_date]", start.isoformat())
                page.fill("input[name=end_date]", end.isoformat())
                page.click("button[value=preview], button[name=action][value=preview]")
                page.wait_for_selector(".preview-check", timeout=5000)
                boxes = page.locator(".preview-check")
                total = boxes.count()
                assert total > 0
                boxes.nth(0).uncheck()
                page.click("button[name=action][value=create]")
                page.wait_for_url("**/bureau/")
                created = self.server.query(
                    "from planning.models import Period;"
                    "print(Period.objects.get(label='Période E2E').events.count())"
                )
                assert int(created) == total - 1, (created, total)

                period_id = self.server.query(
                    "from planning.models import Period;"
                    "print(Period.objects.get(label='Période E2E').pk)"
                )
                page.goto(self.server.url + f"/bureau/periodes/{period_id}/")

                weekday = start + timedelta(days=(0 - start.weekday()) % 7 or 7)
                while weekday.weekday() >= 5 or not (start <= weekday <= end):
                    weekday += timedelta(days=1)
                page.click("summary:text('Ajouter une date en semaine')")
                page.fill("input[name=date]", weekday.isoformat())
                page.select_option("select[name=game_type]", label="Flex")
                page.click("button:text('Ajouter')")
                page.wait_for_url(f"**/bureau/periodes/{period_id}/")
                manual_id = self.server.query(
                    "from planning.models import Event;"
                    f"e=Event.objects.get(date='{weekday.isoformat()}');"
                    "print(e.pk, e.game_type.name, e.origin)"
                )
                assert "Flex" in manual_id, manual_id
                event_pk = manual_id.split()[0]

                dialog_state = {"accept": True}
                page.on(
                    "dialog",
                    lambda d: d.accept() if dialog_state["accept"] else d.dismiss(),
                )
                page.goto(self.server.url + f"/bureau/dates/{event_pk}/")
                page.click("button[value=close]")
                page.wait_for_url(f"**/bureau/dates/{event_pk}/")
                closed = self.server.query(
                    "from planning.models import Event;"
                    f"print(Event.objects.get(pk='{event_pk}').registrations_open)"
                )
                assert closed == "False", closed
                public = self.server.url + f"/dates/{event_pk}/"
                page.goto(public)
                join_hidden = page.eval_on_selector("#join-btn", "el => el.hidden")
                assert join_hidden

                page.goto(self.server.url + f"/bureau/dates/{event_pk}/")
                dialog_state["accept"] = False
                page.click("button[value=cancel]")
                status = self.server.query(
                    "from planning.models import Event;"
                    f"print(Event.objects.get(pk='{event_pk}').status)"
                )
                assert status == "scheduled", status
                dialog_state["accept"] = True

                page.click("button[value=cancel]")
                page.wait_for_url(f"**/bureau/dates/{event_pk}/")
                status = self.server.query(
                    "from planning.models import Event;"
                    f"print(Event.objects.get(pk='{event_pk}').status)"
                )
                assert status == "cancelled", status

                page.click("button[value=restore]")
                page.wait_for_url(f"**/bureau/dates/{event_pk}/")
                state = self.server.query(
                    "from planning.models import Event;"
                    f"e=Event.objects.get(pk='{event_pk}');"
                    "print(e.status, e.registrations_open)"
                )
                assert "scheduled False" in state, state

                page.goto(self.server.url + f"/bureau/periodes/{period_id}/")
                page.click("button[value=archive]")
                page.wait_for_url(f"**/bureau/periodes/{period_id}/")
                archived = self.server.query(
                    "from planning.models import Period;"
                    f"print(Period.objects.get(pk='{period_id}').is_archived)"
                )
                assert archived == "True", archived
                period_edit = page.query_selector(".period-edit")
                assert period_edit is None or not period_edit.is_visible()
                page.screenshot(path=str(SCREENSHOT_DIR / "bureau-archived.png"), full_page=False)

                page.click("button[value=reopen]")
                page.wait_for_url(f"**/bureau/periodes/{period_id}/")
                reopened = self.server.query(
                    "from planning.models import Period;"
                    f"print(Period.objects.get(pk='{period_id}').is_archived)"
                )
                assert reopened == "False", reopened

                page.screenshot(path=str(SCREENSHOT_DIR / "bureau-flow.png"), full_page=False)
                tracked.assert_clean(allow_409=True)
                browser.close()

    def test_dirty_note_survives_remote_close(self):
        with LiveServer() as self.server:
            self.server.seed(seed_period_script())
            organizer_id = self.server.query(
                "from planning.models import Organizer;"
                "print(Organizer.objects.get(name='Volontaire E2E').pk)"
            )
            event_id = self.server.query(
                "from planning.models import Event;print(Event.objects.order_by('date').first().pk)"
            )
            self.server.seed(
                "from planning.services import registrations;"
                "from planning.services.actors import Actor;"
                f"registrations.set_registration('{event_id}', '{organizer_id}',"
                " 'active', actor=Actor.bureau())"
            )
            with browser_tests()() as pw:
                browser, tracked = self._page(pw)
                page = tracked.page
                page.evaluate(
                    "window.ath.pseudo.set({id: '" + organizer_id + "', name: 'Volontaire E2E'})"
                )
                page.goto(self.server.url + "/dates/" + event_id + "/")
                page.wait_for_selector("#registration-form:not([hidden])")
                page.fill("#note-input", "brouillon non sauvegardé")
                self.server.seed(
                    "from planning.services import events;"
                    "from planning.services.actors import Actor;"
                    f"events.set_registrations_open('{event_id}',"
                    " actor=Actor.bureau(), is_open=False)"
                )
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                page.wait_for_selector("#refresh-note:not([hidden])", timeout=8000)
                still = page.eval_on_selector("#note-input", "el => el.value")
                assert still == "brouillon non sauvegardé", still
                resp = None

                def capture(response):
                    nonlocal resp
                    if "/registration/" in response.url and response.request.method == "PUT":
                        resp = response

                page.on("response", capture)
                page.click("#save-note-btn")
                page.wait_for_timeout(800)
                assert resp is not None and resp.status == 409, resp and resp.status
                note_db = self.server.query(
                    "from planning.models import Registration;"
                    "print(repr(Registration.objects.get().note))"
                )
                assert note_db == "''", note_db
                still2 = page.eval_on_selector("#note-input", "el => el.value")
                assert still2 == "brouillon non sauvegardé", still2
                tracked.assert_clean(allow_409=True)
                browser.close()


if __name__ == "__main__":
    unittest.main()
