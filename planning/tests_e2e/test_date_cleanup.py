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


class DateCleanupTests(unittest.TestCase):
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

    def _period_id(self, server):
        return server.query(
            "from planning.models import Period;"
            "print(Period.objects.order_by('start_date').first().pk)"
        )

    def test_cleanup_filters_hide_restore_and_conflict(self):
        with self._server() as server, browser_tests()() as pw:
            e0, e1, e2 = server.query(
                "from planning.models import Event;"
                "print('|'.join(str(e.pk) for e in Event.objects.order_by('date')[:3]))"
            ).split("|")
            server.seed(_register_script(e1, "Seul Inscrit"))
            server.seed(_register_script(e2, "Prenom Un"))
            server.seed(_register_script(e2, "Prenom Deux"))
            period_id = self._period_id(server)
            cleanup_url = server.url + f"/bureau/periodes/{period_id}/nettoyage/"
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
            bureau.goto(cleanup_url)
            expect(bureau.locator("h1")).to_contain_text("Trier les dates")
            assert bureau.locator(f"input[name=event_ids][value='{e0}']").count() == 1
            assert bureau.locator(f"input[name=event_ids][value='{e1}']").count() == 0
            bureau.locator("select[name=staffing]").select_option("one")
            bureau.locator("button:text('Filtrer')").click()
            bureau.wait_for_url("**/nettoyage/?staffing=one")
            assert bureau.locator("input[name=event_ids]").count() == 1
            expect(bureau.locator(".event-card")).to_contain_text("Seul Inscrit")
            check_no_horizontal_overflow(bureau)
            bureau.screenshot(path=str(SCREENSHOT_DIR / "cleanup-filter-390.png"))
            bureau.set_viewport_size({"width": 320, "height": 844})
            check_no_horizontal_overflow(bureau)
            bureau.screenshot(path=str(SCREENSHOT_DIR / "cleanup-filter-320.png"))
            bureau.set_viewport_size({"width": 390, "height": 844})
            bureau.locator("#id_confirm_hide").check()
            bureau.locator("button:text('Masquer les dates')").click()
            bureau.wait_for_url(f"**/bureau/periodes/{period_id}/")
            expect(public.locator(f"#date-{e1}")).to_have_count(0, timeout=9000)
            assert public.locator(f"#date-{e0}").count() == 1
            assert public.locator(f"#date-{e2}").count() == 1
            bureau.goto(server.url + f"/bureau/dates/{e1}/")
            bureau.on("dialog", lambda d: d.accept())
            bureau.locator("button[value=unhide]").click()
            bureau.wait_for_url(f"**/bureau/dates/{e1}/")
            expect(public.locator(f"#date-{e1}")).to_have_count(1, timeout=9000)
            expect(public.locator(f"#date-{e1} .name-registration-form")).to_have_count(1)
            expect(public.locator(f"#date-{e1}")).not_to_contain_text("Inscriptions fermées")
            expect(public.locator(f"#date-{e1} .event-names")).to_contain_text("Seul Inscrit")
            bureau.goto(cleanup_url)
            old_revision = bureau.locator("input[name=expected_revision]").input_value()
            bureau.wait_for_selector(f"input[name=event_ids][value='{e0}']")
            server.seed(_register_script(e2, "Concurrent Nom"))
            bureau.locator("#id_confirm_hide").check()
            bureau.locator("button:text('Masquer les dates')").click()
            expect(bureau.locator(".messages")).to_contain_text("modifié entre-temps")
            hidden = server.query(
                f"from planning.models import Event;print(Event.objects.get(pk='{e0}').is_hidden)"
            )
            assert hidden == "False", hidden
            assert not bureau.locator("#id_confirm_hide").is_checked()
            new_revision = bureau.locator("input[name=expected_revision]").input_value()
            assert new_revision != old_revision, (new_revision, old_revision)
            bureau.locator("#id_confirm_hide").check()
            bureau.locator(f"input[name=event_ids][value='{e0}']").check()
            bureau.locator("button:text('Masquer les dates')").click()
            bureau.wait_for_url(f"**/bureau/periodes/{period_id}/")
            expect(public.locator(f"#date-{e0}")).to_have_count(0, timeout=9000)
            check_no_horizontal_overflow(bureau)
            bureau.goto(cleanup_url)
            bureau.screenshot(path=str(SCREENSHOT_DIR / "cleanup-empty-390.png"))
            browser.close()
