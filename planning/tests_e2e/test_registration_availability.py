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


def _close_flag_script(event_id):
    return (
        "from planning.services import events\n"
        "from planning.services.actors import Actor\n"
        f"events.set_registrations_open('{event_id}', actor=Actor.bureau(),"
        " is_open=False)\n"
    )


class RegistrationAvailabilityTests(unittest.TestCase):
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

    def test_stored_closed_flag_never_blocks_public(self):
        with self._server() as server, browser_tests()() as pw:
            e0, e1 = server.query(
                "from planning.models import Event;"
                "print('|'.join(str(e.pk) for e in Event.objects.order_by('date')[:2]))"
            ).split("|")
            server.seed(_register_script(e1, "Seul Inscrit"))
            server.seed(_close_flag_script(e1))
            server.seed(_close_flag_script(e0))
            browser = pw.chromium.launch()
            public = TrackedPage(
                browser.new_context(viewport={"width": 390, "height": 844}).new_page()
            ).page
            public.goto(server.url + "/")
            row = public.locator(f"#date-{e1}")
            row.wait_for(state="visible", timeout=8000)
            expect(row).to_contain_text("Seul Inscrit")
            expect(row).not_to_contain_text("Inscriptions fermées")
            expect(row.locator(".name-registration-form")).to_have_count(1)
            expect(row.locator(".event-type-select")).to_have_count(1)
            assert public.locator(f"#date-{e1}").get_attribute("data-open") == "true"
            row.locator(".event-type-select").select_option(index=1)
            expect(row.locator(".event-type-select")).not_to_have_value("", timeout=5000)
            empty_row = public.locator(f"#date-{e0}")
            empty_row.locator("input[name=name]").fill("Visiteur Deux")
            empty_row.locator("button[type=submit]").click()
            expect(empty_row).to_contain_text("Visiteur Deux", timeout=8000)
            public.on("dialog", lambda d: d.accept())
            empty_row.locator(".remove-registration").first.click()
            expect(empty_row).not_to_contain_text("Visiteur Deux", timeout=8000)
            empty_row.locator("input[name=name]").fill("Visiteur Deux")
            empty_row.locator("button[type=submit]").click()
            expect(empty_row).to_contain_text("Visiteur Deux", timeout=8000)
            bureau = TrackedPage(
                browser.new_context(viewport={"width": 390, "height": 844}).new_page()
            ).page
            self._bureau_login(bureau, server)
            bureau.locator("a:text('Gérer')").first.click()
            bureau.wait_for_url("**/bureau/periodes/*/")
            content = bureau.content()
            for text in (
                "Fermée",
                "Fermer les inscriptions",
                "Ouvrir les inscriptions",
                "Clôturer les inscriptions",
            ):
                assert text not in content, text
            brow = bureau.locator(".bureau-event", has_text="Seul Inscrit")
            brow.locator("input[name=visible]").uncheck()
            expect(brow.locator(".visibility-status")).to_be_visible()
            expect(public.locator(f"#date-{e1}")).to_have_count(0, timeout=9000)
            expect(brow).to_contain_text("Seul Inscrit")
            brow.locator("input[name=visible]").check()
            expect(brow.locator(".visibility-status")).to_be_hidden()
            expect(public.locator(f"#date-{e1}")).to_have_count(1, timeout=9000)
            expect(public.locator(f"#date-{e1}")).to_contain_text("Seul Inscrit")
            expect(public.locator(f"#date-{e1} .name-registration-form")).to_have_count(1)
            assert public.locator(f"#date-{e1}").get_attribute("data-open") == "true"
            browser.close()
