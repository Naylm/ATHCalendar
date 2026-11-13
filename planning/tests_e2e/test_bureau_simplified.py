import re
import unittest
from contextlib import contextmanager

from playwright.sync_api import expect

from planning.tests_e2e.test_ui import (
    BUREAU_PASSWORD,
    SCREENSHOT_DIR,
    SEED_BUREAU,
    LiveServer,
    TrackedPage,
    browser_tests,
    check_no_horizontal_overflow,
    seed_period_script,
)


def _set_active():
    return (
        "from planning.services import periods\n"
        "from planning.services.actors import Actor\n"
        "from planning.models import Period\n"
        "p = Period.objects.order_by('start_date').first()\n"
        "periods.set_active(p.pk, actor=Actor.bureau())\n"
    )


def _register_script(event_id, name):
    return (
        "from planning.services import organizers, registrations\n"
        "from planning.services.actors import Actor\n"
        f"o = organizers.get_or_create_organizer({name!r})\n"
        f"registrations.set_registration('{event_id}', o.pk, 'active',"
        " actor=Actor.public(o))\n"
    )


class BureauSimplifiedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if browser_tests() is None:
            raise unittest.SkipTest("playwright non disponible")

    def setUp(self):
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _server(self):
        with LiveServer() as server:
            server.seed(seed_period_script())
            server.seed(_set_active())
            server.seed(SEED_BUREAU)
            yield server

    def _bureau_login(self, page, server):
        page.goto(server.url + "/bureau/")
        page.fill("input[type=password]", BUREAU_PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url("**/bureau/")

    def test_visibility_checkbox_flow(self):
        with self._server() as server, browser_tests()() as pw:
            e0, e1, e2 = server.query(
                "from planning.models import Event;"
                "print('|'.join(str(e.pk) for e in Event.objects.order_by('date')[:3]))"
            ).split("|")
            server.seed(_register_script(e1, "Seul Inscrit"))
            server.seed(_register_script(e2, "Prenom Un"))
            server.seed(_register_script(e2, "Prenom Deux"))
            browser = pw.chromium.launch()
            public = TrackedPage(
                browser.new_context(viewport={"width": 390, "height": 844}).new_page()
            ).page
            public.goto(server.url + "/")
            public.wait_for_selector(f"#date-{e1}", timeout=8000)
            bureau = TrackedPage(
                browser.new_context(viewport={"width": 390, "height": 844}).new_page()
            ).page
            self._bureau_login(bureau, server)
            assert bureau.locator("details.card").first.get_attribute("open") is None
            assert not bureau.locator("a[href='/bureau/exports/donnees.json']").is_visible()
            assert bureau.locator("a:text('Créer une période')").is_visible()
            assert bureau.locator("a:text('Gérer')").first.is_visible()
            assert not bureau.locator("a:text('Export CSV')").is_visible()
            bureau.locator("a:text('Gérer')").first.click()
            bureau.wait_for_url("**/bureau/periodes/*/")
            expect(bureau.locator("h1")).to_contain_text("Semestre test")
            assert "Périodes" not in bureau.locator("h1").inner_text()
            back_font = bureau.locator(".period-back a").evaluate(
                "el => getComputedStyle(el).fontSize"
            )
            assert float(back_font[:-2]) <= 16, back_font
            assert bureau.locator("main button[type=submit]:visible").count() == 0
            assert bureau.locator("button[data-filter-submit]").is_hidden()
            rows = bureau.locator(".bureau-event").count()
            assert rows >= 3
            assert bureau.locator("input[name=visible]").count() == rows
            assert bureau.locator("input[name=visible]:checked").count() == rows
            assert bureau.locator("input[name=event_ids]").count() == 0
            assert bureau.locator("#select-all-events").count() == 0
            assert bureau.locator("button[name=confirm]").count() == 0
            assert bureau.locator("select[name=selection_action]").count() == 0
            options = bureau.locator("details.card", has_text="Options")
            assert options.get_attribute("open") is None
            assert not bureau.locator("button[value=archive]").is_visible()
            assert bureau.locator("a:text('Clôturer les inscriptions')").count() == 0
            assert bureau.locator("button[value=open_all]").count() == 0
            assert bureau.locator("button[value=close_all]").count() == 0
            options.locator("summary").first.click()
            assert bureau.locator("button[value=archive]").is_visible()
            assert bureau.locator("input[name=label]").is_visible()
            options.locator("summary").first.click()
            assert options.get_attribute("open") is None
            bureau.locator("select[name=staffing]").select_option("one")
            bureau.wait_for_url("**/periodes/*/?staffing=one")
            assert bureau.locator(".bureau-event").count() == 1
            row = bureau.locator(".bureau-event", has_text="Seul Inscrit")
            expect(row.locator(".visibility-status")).to_be_hidden()
            assert row.locator("input[name=visible]").is_checked()
            row.locator("input[name=visible]").uncheck()
            expect(row.locator(".visibility-status")).to_be_visible()
            expect(row).to_have_class(re.compile("is-hidden"))
            expect(row).to_contain_text("Seul Inscrit")
            expect(public.locator(f"#date-{e1}")).to_have_count(0, timeout=9000)
            assert "staffing=one" in bureau.url
            assert bureau.locator(f"input[name=event_id][value='{e1}']").count() == 1
            server.seed(_register_script(e0, "Concurrence"))
            row.locator("input[name=visible]").check()
            expect(row.locator(".visibility-msg")).to_be_visible()
            assert not row.locator("input[name=visible]").is_checked()
            expect(row.locator(".visibility-status")).to_be_visible()
            row.locator("input[name=visible]").check()
            expect(row.locator(".visibility-msg")).to_be_hidden()
            expect(row.locator(".visibility-status")).to_be_hidden()
            expect(public.locator(f"#date-{e1}")).to_have_count(1, timeout=9000)
            expect(public.locator(f"#date-{e1} .name-registration-form")).to_have_count(1)
            expect(public.locator(f"#date-{e1} .event-names")).to_contain_text("Seul Inscrit")
            assert "staffing=one" in bureau.url
            bureau.locator("select[name=staffing]").select_option("all")
            bureau.wait_for_url("**/periodes/*/?staffing=all")
            assert bureau.locator(".bureau-event").count() == rows
            hidden_row = bureau.locator(".bureau-event", has_text="Prenom Un")
            hidden_row.locator("input[name=visible]").uncheck()
            expect(hidden_row.locator(".visibility-status")).to_be_visible()
            expect(public.locator(f"#date-{e2}")).to_have_count(0, timeout=9000)
            expect(public.locator(f"#date-{e0}")).to_have_count(1)
            expect(hidden_row).to_contain_text("Prenom Un")
            check_no_horizontal_overflow(bureau)
            bureau.screenshot(path=str(SCREENSHOT_DIR / "bureau-visibility-390.png"))
            bureau.set_viewport_size({"width": 320, "height": 844})
            check_no_horizontal_overflow(bureau)
            bureau.screenshot(path=str(SCREENSHOT_DIR / "bureau-visibility-320.png"))
            bureau.set_viewport_size({"width": 1440, "height": 900})
            check_no_horizontal_overflow(bureau)
            bureau.screenshot(path=str(SCREENSHOT_DIR / "bureau-visibility-1440.png"))
            browser.close()
